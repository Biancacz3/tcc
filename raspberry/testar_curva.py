#!/usr/bin/env python3
"""Uma sequencia experimental de curva esquerda. Nao instalar como servico.

Requer testar_conjunto.py V2 e testar_motores.py nesta mesma pasta.
Cinco pulsos (0.45, 0.45, 0.45, 0.45, 0.1 s), PWM 45%, pausas de 1 s.
Nao mede angulo e nao promete 90 graus. Executa uma vez e para.
Sem argumentos ou com --autoteste: nenhum GPIO acessado.
Ctrl+C e sinais de encerramento solicitam parada; nao substituem corte fisico
em caso de falha do sistema, alimentacao ou hardware.
"""
import argparse
import signal
import sys
import time

from testar_conjunto import pulse_all, validate
from testar_motores import Outputs, interrupted, process_lock

PULSES = (0.45, 0.45, 0.45, 0.45, 0.1)
PAUSE = 1.0
POWER = 45


def sequence(outputs, wait=time.sleep):
    try:
        for duration in PULSES:
            validate("esquerda", duration, POWER)
        outputs.stop()
        for index, duration in enumerate(PULSES):
            pulse_all(outputs, "esquerda", duration, wait=wait, power=POWER)
            if index < len(PULSES) - 1:
                # pulse_all ja desligou todas as habilitacoes e direcoes.
                wait(PAUSE)
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
    expected = [0.45, 1.0, 0.45, 1.0, 0.45, 1.0, 0.45, 1.0, 0.1]
    observed = []
    def inspect(seconds):
        index = len(observed)
        assert seconds == expected[index]
        if index % 2 == 0:
            assert {p for p, x in outputs.inputs.items() if x.value} == {27, 23, 5, 13}
            assert all(x.value == 0.45 for x in outputs.enables.values())
        else:
            assert all(x.value == 0 for x in pins.values())
        observed.append(seconds)
    sequence(outputs, inspect)
    assert observed == expected
    assert all(x.value == 0 for x in pins.values())
    # Falha em cada pulso e em cada pausa: nenhuma fase posterior e executada.
    for error in (RuntimeError, KeyboardInterrupt):
        for failure_at in range(9):
            calls = []
            def fail(seconds):
                calls.append(seconds)
                if len(calls) == failure_at + 1:
                    raise error("interrupcao simulada")
            try:
                sequence(outputs, fail)
            except error:
                pass
            else:
                raise AssertionError("Interrupcao nao propagada")
            assert len(calls) == failure_at + 1
            assert all(x.value == 0 for x in pins.values())
    outputs.close()
    assert all(x.closed for x in pins.values())
    print("AUTOTESTE CURVA V2 OK: sequencia, pausas desligadas e 18 interrupcoes verificadas.")
    print("Nenhum GPIO acessado. O angulo real precisa ser observado no chao.")


def timed_wait(seconds):
    # Limite por fase, alem do finally. Nao e watchdog de hardware.
    signal.setitimer(signal.ITIMER_REAL, seconds + 0.7)
    try:
        time.sleep(seconds)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def run_real():
    for duration in PULSES:
        validate("esquerda", duration, POWER)
    if not sys.stdin.isatty():
        raise RuntimeError("Use terminal interativo; nao executar como servico")
    print("CURVA ESQUERDA V2: 4 x 0.45 s + 1 x 0.1 s; PWM 45%; pausas de 1 s.")
    print("Uma unica sequencia, cerca de 5.9 s com as pausas. Angulo nao garantido.")
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
            command = input("Digite CURVA para executar os CINCO pulsos automaticamente: ").strip()
            if command == "CURVA":
                sequence(outputs, timed_wait)
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
    args = parser.parse_args()
    if args.autoteste:
        selftest()
    elif args.executar:
        run_real()
    else:
        print("Nenhum GPIO acessado. Use --autoteste ou --executar.")
        print("Esquerda: 4 x 0.45 s + 1 x 0.1 s; PWM 45%; pausas 1 s; sem repeticao.")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"CURVA INTERROMPIDA: {exc}. Desligue a alimentacao dos motores.", file=sys.stderr)
        sys.exit(1)
