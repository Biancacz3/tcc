"""Testes de integracao com SQLite e modelo ONNX sintetico; sem pesos privados."""
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

os.environ.update(ADMIN_API_TOKEN='a'*64, ALLOW_LEGACY_ADMIN_TOKEN='true', DB_HOST='localhost', DB_PORT='5432',
                  POSTGRES_DB='test', POSTGRES_USER='test', POSTGRES_PASSWORD='test')
import numpy as np
import onnx
import pytest
from fastapi.testclient import TestClient
from onnx import TensorProto, helper, numpy_helper
from PIL import Image
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.inference import Detector, decode, preprocess
from app.main import app
from app.api import analyses
from app.models import Base, Device, Capture, Analysis
from app.db.session import get_session


def make_model(path, confidence=.9):
    output = np.zeros((1,5,8400), dtype=np.float32)
    output[0,:,0] = [320,320,200,100,confidence]
    graph = helper.make_graph([helper.make_node('Constant', [], ['output0'],
                              value=numpy_helper.from_array(output))], 'test',
                              [helper.make_tensor_value_info('images',TensorProto.FLOAT,[1,3,640,640])],
                              [helper.make_tensor_value_info('output0',TensorProto.FLOAT,[1,5,8400])])
    model=helper.make_model(graph,opset_imports=[helper.make_opsetid('',17)])
    model.ir_version=10
    helper.set_model_props(model, {'names': "{0: 'pulgao'}", 'task':'detect'})
    onnx.save(model,path)
    return Detector(path)


def service_fixture(detector):
    """Isolate persistence/HTTP tests from plant segmentation.

    This fixture's uniform 100x50 image is intentionally not a plant. The
    real ONNX decoder still runs; candidate localization is covered separately
    in test_region_inference and by the two-photo release evaluation.
    """
    def analyze(rgb, marker_id=None):
        boxes = detector._predict_crop(rgb)
        return {"detections": boxes, "candidates": [],
                "status": "positive" if boxes else "no_detections",
                "warnings": [], "method": "synthetic_service_fixture",
                "plant_bbox": None}
    detector.analyze = analyze
    return detector


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
    @event.listens_for(engine,'connect')
    def functions(connection, _):
        connection.create_function('now',0,lambda: datetime.now(timezone.utc).isoformat())
        connection.execute('PRAGMA foreign_keys=ON')
        connection.isolation_level = None
    @event.listens_for(engine, 'begin')
    def begin(connection):
        connection.exec_driver_sql('BEGIN')
    Base.metadata.create_all(engine)
    session=Session(engine)
    device=Device(id=uuid4(),name='Teste',is_active=True)
    session.add(device);session.flush()
    photo=tmp_path/'photo.jpg';Image.new('RGB',(100,50),(25,160,55)).save(photo)
    data=photo.read_bytes()
    capture=Capture(id=uuid4(),device_id=device.id,captured_at=datetime.now(timezone.utc),
        storage_key='photo.jpg',sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data),width=100,height=50)
    session.add(capture);session.commit()
    detector=service_fixture(make_model(tmp_path/'model.onnx'))
    monkeypatch.setattr(analyses,'STORAGE_DIR',tmp_path)
    monkeypatch.setattr(analyses,'get_detector',lambda:detector)
    def dependency():
        yield session
    app.dependency_overrides[get_session]=dependency
    with TestClient(app) as client:
        yield client,session,capture,photo,detector
    app.dependency_overrides.clear();session.close();engine.dispose()


HEADERS={'Authorization':'Bearer '+'a'*64}

def url(capture): return f'/api/v1/captures/{capture.id}/analyses'


def test_persist_repeat_and_read(setup):
    c,s,cap,photo,det=setup
    original=photo.read_bytes()
    first=c.post(url(cap),headers=HEADERS)
    assert first.status_code==201,first.text
    row=first.json()
    assert row['detection_count']==1 and row['result']=='positive'
    assert row['analysis_state']=='positive'
    assert row['model_sha256']==det.sha256
    assert np.allclose(row['detections'][0]['xyxy'],[34.375,17.1875,65.625,32.8125])
    again=c.post(url(cap),headers=HEADERS)
    assert again.status_code==200 and again.json()['id']==row['id']
    assert s.scalar(select(func.count()).select_from(Analysis))==1
    assert c.get(url(cap),headers=HEADERS).json()[0]['id']==row['id']
    assert photo.read_bytes()==original


def test_unauthorized(setup):
    c,s,cap,*_=setup
    for headers in ({},{'Authorization':'Bearer camera-token'}):
        assert c.post(url(cap),headers=headers).status_code==401
        assert c.get(url(cap),headers=headers).status_code==401
    assert s.scalar(select(func.count()).select_from(Analysis))==0


def test_busy_missing_and_empty(setup):
    c,s,cap,*_=setup
    assert c.get(url(cap),headers=HEADERS).json()==[]
    assert c.post(f'/api/v1/captures/{uuid4()}/analyses',headers=HEADERS).status_code==404
    analyses.analysis_lock.acquire()
    try:
        assert c.post(url(cap),headers=HEADERS).status_code==503
    finally: analyses.analysis_lock.release()


def test_corrupted_image_not_saved(setup):
    c,s,cap,photo,_=setup
    photo.write_bytes(b'broken')
    assert c.post(url(cap),headers=HEADERS).status_code==503
    assert s.scalar(select(func.count()).select_from(Analysis))==0


def test_missing_model_preserves_api(setup,monkeypatch):
    c,s,cap,*_=setup
    def broken(): raise FileNotFoundError('missing model')
    monkeypatch.setattr(analyses,'get_detector',broken)
    assert c.post(url(cap),headers=HEADERS).status_code==503
    assert c.get('/api/v1/health').status_code==200
    assert c.get(url(cap),headers=HEADERS).json()==[]


def test_failed_commit_not_success(setup,monkeypatch):
    c,s,cap,*_=setup
    def failed(): raise SQLAlchemyError('database failed')
    monkeypatch.setattr(s,'commit',failed)
    assert c.post(url(cap),headers=HEADERS).status_code==503
    assert s.scalar(select(func.count()).select_from(Analysis))==0
    assert not analyses.analysis_lock.locked()


def test_no_detections_and_model_history(setup,tmp_path,monkeypatch):
    c,s,cap,*_=setup
    first=c.post(url(cap),headers=HEADERS).json()
    empty=service_fixture(make_model(tmp_path/'empty.onnx',confidence=.1))
    monkeypatch.setattr(analyses,'get_detector',lambda:empty)
    other=c.post(url(cap),headers=HEADERS)
    assert other.status_code==201
    assert other.json()['result']=='no_detections'
    assert other.json()['detections']==[]
    assert len(c.get(url(cap),headers=HEADERS).json())==2
    assert other.json()['id']!=first['id']


def test_nms_letterbox_empty():
    tensor,ratio,left,top=preprocess(np.zeros((200,100,3),dtype=np.uint8))
    assert tensor.shape==(1,3,640,640) and (ratio,left,top)==(3.2,160,0)
    output=np.zeros((1,5,8400),np.float32)
    output[0,:,0]=[320,320,100,100,.9]
    output[0,:,1]=[320,320,100,100,.8]
    boxes=decode(output,ratio,left,top,100,200)
    assert len(boxes)==1
    assert np.allclose(boxes[0]['xyxy'],[34.375,84.375,65.625,115.625])
    assert decode(np.zeros_like(output),ratio,left,top,100,200)==[]


def test_reject_other_model(tmp_path):
    make_model(tmp_path/'model.onnx')
    model=onnx.load(tmp_path/'model.onnx')
    helper.set_model_props(model,{'names':"{0: 'person'}",'task':'detect'})
    onnx.save(model,tmp_path/'wrong.onnx')
    with pytest.raises(ValueError): Detector(tmp_path/'wrong.onnx')


def test_migration_matches_models():
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.autogenerate import compare_metadata
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine,tables=[Device.__table__])
    versions=Path(__file__).resolve().parents[1]/'backend/alembic/versions'
    with engine.begin() as connection:
        connection.connection.create_function('now',0,lambda: datetime.now(timezone.utc).isoformat())
        context=MigrationContext.configure(connection)
        for name in ('0003_captures.py', '0004_analyses.py', '0005_jobs_plants.py', '0006_auth.py', '0007_analysis_regions.py'):
            spec=importlib.util.spec_from_file_location('migration',versions/name)
            migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
            with Operations.context(context): migration.upgrade()
        assert compare_metadata(context,Base.metadata)==[]
    engine.dispose()


def test_fractional_padding_matches_ultralytics_scale_boxes():
    # 435 x 2819: letterbox usa esquerda=270; scale_boxes 8.3.203 usa 271.
    width,height=435,2819
    ratio=min(640/width,640/height)
    output=np.zeros((1,5,8400),np.float32)
    output[0,:,0]=[320,320,40,100,.9]
    result=decode(output,ratio,270,0,width,height)[0]['xyxy']
    expected=[(300-271)/ratio,270/ratio,(340-271)/ratio,370/ratio]
    assert np.allclose(result,expected,atol=1e-4)
    assert abs(result[0]-(300-270)/ratio)>1
