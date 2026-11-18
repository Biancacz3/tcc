#!/usr/bin/env python3
"""Teste isolado: esquerda, avanco, esquerda. Uma vez; sem camera ou servico.
Requer testar_conjunto.py V3 e testar_motores.py na mesma pasta.
Trajetoria por tempo: nao mede distancia, angulo ou obstaculos.
Primeiro teste no chao em area livre, supervisionado, com corte fisico acessivel.
V4: tres pulsos ajustaveis por curva (padrao 0.40 s), mais dois de 0.10 s.
--etapas exige CONTINUAR apos curva 1 e avanco, com todos os GPIO em zero.
"""
import argparse
import signal
import sys
import time

from testar_conjunto import pulse_all, validate
from testar_motores import Outputs, interrupted, process_lock

# nome, movimento, duracoes, PWM base, reducao esquerda em pontos percentuais.
def make_plan(turn1=0.40, turn2=0.40):
    # Valida ambos antes de configurar GPIO ou executar o primeiro movimento.
    validate("esquerda", turn1, 45, 0)
    validate("esquerda", turn2, 45, 0)
    return (
        ("CURVA 1", "esquerda", (turn1,) * 3 + (0.1, 0.1), 45, 0),
        ("AVANCO", "frente", (0.3,) * 6, 35, 5),
        ("CURVA 2", "esquerda", (turn2,) * 3 + (0.1, 0.1), 45, 0),
    )


STAGES = make_plan()


def check_plan(stages=STAGES):
    for _, move, pulses, power, reduction in stages:
        for duration in pulses:
            validate(move, duration, power, reduction)


def sequence(outputs, wait=time.sleep, report=lambda message: None,
             stages=STAGES, checkpoint=None):
    try:
        check_plan(stages)
        outputs.stop()
        for stage_index, (name, move, pulses, power, reduction) in enumerate(stages):
            report(name)
            for index, duration in enumerate(pulses):
                pulse_all(outputs, move, duration, wait=wait, power=power,
                          left_reduction=reduction)
                if index < len(pulses) - 1:
                    wait(1.0)
            if stage_index < len(stages) - 1:
                wait(2.0)
                if checkpoint is not None:
                    outputs.stop()
                    checkpoint(name)
    finally:
        outputs.stop()


def selftest():
    pins = {}
    class Fake:
        def __init__(self, pin, initial_value, **kwargs):
            self.value, self.closed = initial_value, False
            pins[pin] = self
        def on(self): self.value = 1
        def off(self): self.value = 0
        def close(self): self.closed = True
    outputs = Outputs(Fake, Fake, None)
    # Oraculo independente do plano: 5 pulsos curva, 6 avanco, 5 curva.
    expected_times = (
        [0.40, 1, 0.40, 1, 0.40, 1, 0.10, 1, 0.10, 2]
        + [0.3, 1, 0.3, 1, 0.3, 1, 0.3, 1, 0.3, 1, 0.3, 2]
        + [0.40, 1, 0.40, 1, 0.40, 1, 0.10, 1, 0.10]
    )
    calls, phases = [], []
    def inspect(seconds):
        index = len(calls)
        assert seconds == expected_times[index]
        if seconds >= 1:
            assert all(x.value == 0 for x in pins.values())
        else:
            forward = 10 <= index <= 20
            high = {17, 23, 6, 13} if forward else {27, 23, 5, 13}
            assert {p for p, x in outputs.inputs.items() if x.value} == high
            expected_pwm = ({12: .30, 18: .35, 20: .30, 21: .35} if forward
                            else {12: .45, 18: .45, 20: .45, 21: .45})
            assert {p: x.value for p, x in outputs.enables.items()} == expected_pwm
        calls.append(seconds)
    sequence(outputs, inspect, phases.append)
    assert calls == expected_times and phases == ['CURVA 1', 'AVANCO', 'CURVA 2']
    assert abs(sum(calls) - 21.6) < 1e-8
    assert all(x.value == 0 for x in pins.values())
    for failure in (RuntimeError, KeyboardInterrupt):
        for failure_at in range(len(expected_times)):
            seen = []
            def fail(seconds):
                seen.append(seconds)
                if len(seen) == failure_at + 1:
                    raise failure('interrupcao simulada')
            try:
                sequence(outputs, fail)
            except failure:
                pass
            else:
                raise AssertionError('Falha nao propagada')
            assert len(seen) == failure_at + 1
            assert all(x.value == 0 for x in pins.values())
    custom = []
    checkpoints = []
    def inspect_checkpoint(name):
        assert all(x.value == 0 for x in pins.values())
        checkpoints.append(name)
    sequence(outputs, custom.append, stages=make_plan(.35, .42),
             checkpoint=inspect_checkpoint)
    assert custom == ([.35, 1, .35, 1, .35, 1, .1, 1, .1, 2]
                      + [.3, 1, .3, 1, .3, 1, .3, 1, .3, 1, .3, 2]
                      + [.42, 1, .42, 1, .42, 1, .1, 1, .1])
    assert checkpoints == ['CURVA 1', 'AVANCO']
    for index in (0, 1):
        reached = []
        def cancel_checkpoint(name):
            assert all(x.value == 0 for x in pins.values())
            raise KeyboardInterrupt('cancelado na pausa')
        def cancel_selected(name):
            if name == ('CURVA 1', 'AVANCO')[index]:
                cancel_checkpoint(name)
        try:
            sequence(outputs, lambda seconds: None, reached.append,
                     checkpoint=cancel_selected)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError('Cancelamento nao propagado')
        assert reached == ['CURVA 1', 'AVANCO'][:index + 1]
        assert all(x.value == 0 for x in pins.values())
    for bad in (0, .09, .51, float('nan'), float('inf')):
        for args in ((bad, .4), (.4, bad)):
            try:
                make_plan(*args)
            except ValueError:
                pass
            else:
                raise AssertionError('Tempo invalido aceito')
    outputs.close()
    assert all(x.closed for x in pins.values())
    print('AUTOTESTE TROCA V4 OK: tres pulsos principais e dois curtos por curva; parada verificada.')
    print('Nenhum GPIO acessado. Distancia e alinhamento exigem teste fisico.')


def timed_wait(seconds):
    # Limite por fase, alem do finally. Nao e watchdog de hardware.
    signal.setitimer(signal.ITIMER_REAL, seconds + 0.7)
    try:
        time.sleep(seconds)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def run_real(args):
    stages = make_plan(args.pulso_curva1, args.pulso_curva2)
    check_plan(stages)
    if not sys.stdin.isatty():
        raise RuntimeError("Use terminal interativo; nao executar como servico")
    print("TROCA V4: curva esquerda, seis pulsos para frente, curva esquerda.")
    print(f"Curva 1: 3 x {args.pulso_curva1:.2f} s + 2 x 0.10 s, PWM 45%.")
    print(f"Curva 2: 3 x {args.pulso_curva2:.2f} s + 2 x 0.10 s, PWM 45%.")
    print("Uma unica sequencia. Distancia e angulos nao garantidos.")
    if args.etapas:
        print("MODO POR ETAPAS: so prossegue apos CONTINUAR; pode cancelar com Ctrl+C.")
    print("Avanco: PWM esquerdo 30%, direito 35%. Pausas entre etapas: 2 s.")
    print("Comece com alimentacao dos motores desligada e area livre.")
    if input("Digite PREPARADO: ").strip() != "PREPARADO":
        print("Cancelado sem acessar GPIO.")
        return
    with process_lock():
        from gpiozero import DigitalOutputDevice, PWMOutputDevice
        from gpiozero.pins.lgpio import LGPIOFactory
        factory = LGPIOFactory(chip=0)
        outputs = None
        handlers = {}
        try:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGALRM):
                handlers[sig] = signal.signal(sig, interrupted)
            outputs = Outputs(DigitalOutputDevice, PWMOutputDevice, factory)
            print("GPIO em zero. Ligue os motores; se girarem agora, desligue e cancele.")
            command = input("Digite TROCA para iniciar a sequencia: ").strip()
            if command == "TROCA":
                def checkpoint(name):
                    print(f"{name} encerrado. GPIO em zero. Confira a posicao sem mover o carrinho.")
                    if input("Digite CONTINUAR para a proxima etapa ou Enter para cancelar: ").strip() != "CONTINUAR":
                        raise KeyboardInterrupt("Cancelado entre etapas")
                sequence(outputs, timed_wait, lambda msg: print(msg, flush=True),
                         stages=stages, checkpoint=checkpoint if args.etapas else None)
                print("SEQUENCIA CONCLUIDA: motores desligados nos GPIO. Nao repete.")
            else:
                print("Sequencia cancelada; GPIO em zero.")
            input("Desligue a alimentacao dos motores; depois pressione Enter para encerrar. ")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            try:
                if outputs is not None:
                    outputs.close()
            finally:
                try:
                    factory.close()
                finally:
                    for sig, handler in handlers.items():
                        signal.signal(sig, handler)
            print("Encerrado. Mantenha a alimentacao dos motores desligada.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--autoteste", action="store_true")
    mode.add_argument("--executar", action="store_true")
    parser.add_argument("--pulso-curva1", type=float, default=.40,
                        help="Duracao de cada um dos tres pulsos da curva 1; padrao .40 s")
    parser.add_argument("--pulso-curva2", type=float, default=.40,
                        help="Duracao de cada um dos tres pulsos da curva 2; padrao .40 s")
    parser.add_argument("--etapas", action="store_true",
                        help="Aguarda CONTINUAR com motores desligados entre as tres etapas")
    args = parser.parse_args()
    if args.autoteste:
        selftest()
    elif args.executar:
        run_real(args)
    else:
        print("Nenhum GPIO acessado. Use --autoteste ou --executar.")
        print("Esquerda -> avanco -> esquerda; uma vez, sem camera e sem servico.")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"TROCA INTERROMPIDA: {exc}. Desligue a alimentacao dos motores.", file=sys.stderr)
        sys.exit(1)
