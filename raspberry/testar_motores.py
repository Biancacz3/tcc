#!/usr/bin/env python3
"""Teste manual de UM canal L298N. Nunca instalar como servico.

Sem argumentos: imprime mapa, sem importar GPIO. --autoteste: somente objetos
simulados. Modo fisico: --executar --canal frente-a [--inverter].
So usar modo fisico depois de conferir alimentacao, ENA/ENB e GND, com rodas
suspensas. Os motores sao de 3-6 V. PWM nao substitui alimentacao adequada.
O programa autonomo continua inalterado. Nao identifica qual roda foi ligada
a cada canal: isso sera observado no teste. Software nao substitui corte de
energia nem resistores de pull-down nas habilitacoes para manter parada no boot.
Um travamento do sistema/perda de energia nao e coberto pelo finally Python.

Referencia: https://gpiozero.readthedocs.io/en/stable/api_output.html
https://gpiozero.readthedocs.io/en/stable/api_pins.html
"""
import argparse
import math
import signal
import sys
import time
from contextlib import contextmanager
from pathlib import Path

# nome: IN positivo, IN negativo, enable (BCM, nao numeracao fisica)
CHANNELS = {
    "frente-a": (17, 27, 12),
    "frente-b": (22, 23, 18),
    "tras-a": (5, 6, 20),
    "tras-b": (13, 19, 21),
}
PHYSICAL = {17: 11, 27: 13, 12: 32, 22: 15, 23: 16, 18: 12,
            5: 29, 6: 31, 20: 38, 13: 33, 19: 35, 21: 40}


class Outputs:
    def __init__(self, digital, pwm, factory):
        self.enables = {}
        self.inputs = {}
        try:
            # Todas as habilitacoes ficam baixas antes de configurar direcoes.
            for _, _, en in CHANNELS.values():
                self.enables[en] = pwm(en, initial_value=0, frequency=100,
                                       pin_factory=factory)
            for a, b, _ in CHANNELS.values():
                for pin in (a, b):
                    self.inputs[pin] = digital(pin, initial_value=False,
                                               pin_factory=factory)
        except BaseException:
            self.close()
            raise

    def stop(self):
        errors = []
        for output in (*self.enables.values(), *self.inputs.values()):
            try:
                output.off()
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("Falha ao desligar GPIO: " + "; ".join(errors))

    def close(self):
        try:
            self.stop()
        finally:
            for output in (*self.enables.values(), *self.inputs.values()):
                try:
                    output.close()
                except Exception as exc:
                    print(f"Falha ao liberar GPIO: {exc}", file=sys.stderr)

    def pulse(self, channel, invert=False, duration=0.3, duty=0.35, wait=time.sleep):
        if channel not in CHANNELS:
            raise ValueError("Canal invalido")
        if not math.isfinite(duration) or not 0 < duration <= 0.5:
            raise ValueError("Pulso deve estar entre 0 e 0.5 segundo")
        if not math.isfinite(duty) or not 0 < duty <= 0.5:
            raise ValueError("Duty deve estar entre 0 e 0.5")
        self.stop()
        a, b, en = CHANNELS[channel]
        try:
            self.inputs[b if invert else a].on()
            self.enables[en].value = duty
            wait(duration)
        finally:
            self.stop()


@contextmanager
def process_lock():
    import fcntl
    base = Path.home() / "tcc-raspberry"
    if not base.is_dir():
        raise RuntimeError("Pasta ~/tcc-raspberry nao encontrada")
    # Mesmo lock do agente: nao concorre com camera/autonomo/painel.
    with (base / ".agent.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Pare tcc-autonomo, tcc-painel e tcc-camera antes do teste") from None
        yield


def selftest():
    created = []
    class Fake:
        def __init__(self, pin, initial_value, **kwargs):
            self.pin, self.value, self.closed = pin, initial_value, False
            created.append(self)
        def off(self): self.value = 0
        def on(self): self.value = 1
        def close(self): self.closed = True

    outputs = Outputs(Fake, Fake, None)
    assert [x.pin for x in created[:4]] == [12, 18, 20, 21]
    for name, (a, b, en) in CHANNELS.items():
        for invert in (False, True):
            def observe(seconds):
                assert seconds == 0.3
                active = {x.pin for x in created if x.value}
                assert active == {en, b if invert else a}, active
            outputs.pulse(name, invert, wait=observe)
            assert all(x.value == 0 for x in created)
    for failure in (RuntimeError, KeyboardInterrupt):
        def fail(seconds): raise failure("Interrupcao simulada")
        try:
            outputs.pulse("frente-a", wait=fail)
        except failure:
            pass
        else:
            raise AssertionError("Interrupcao nao propagada")
        assert all(x.value == 0 for x in created)
    for args in ({"duration": 5}, {"duty": 1}, {"duration": float('nan')}):
        try:
            outputs.pulse("frente-a", **args)
        except ValueError:
            pass
        else:
            raise AssertionError("Limite nao aplicado")
    outputs.close()
    assert all(x.closed for x in created)
    created.clear()
    def broken(pin, **kwargs): raise RuntimeError("Falha de inicializacao simulada")
    try:
        Outputs(broken, Fake, None)
    except RuntimeError:
        pass
    assert len(created) == 4 and all(x.closed and x.value == 0 for x in created)
    print("AUTOTESTE OK: quatro canais, dois sentidos, parada e limites verificados.")
    print("Nenhum GPIO foi acessado e nenhum motor foi acionado.")


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"Sinal {signum}")


def physical_test(args):
    if not sys.stdin.isatty():
        raise RuntimeError("Teste real exige terminal interativo; nao executar como servico")
    if not args.canal:
        raise RuntimeError("Informe --canal")
    print("MODO FISICO: um pulso de 0.3 segundo, PWM 35%.")
    print("Antes de iniciar: rodas suspensas, alimentacao dos motores DESLIGADA,")
    print("Pi com fonte adequada separada da saida 5V do L298N, tensao dos motores")
    print("conferida, GND comum e jumpers ENA/ENB removidos. Sem esses requisitos, saia.")
    if input("Digite PREPARADO para configurar os GPIO desligados: ").strip() != "PREPARADO":
        print("Cancelado sem acessar GPIO.")
        return
    with process_lock():
        from gpiozero import DigitalOutputDevice, PWMOutputDevice
        from gpiozero.pins.lgpio import LGPIOFactory
        factory = LGPIOFactory(chip=0)
        outputs = None
        old = {}
        try:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGALRM):
                old[sig] = signal.signal(sig, interrupted)
            outputs = Outputs(DigitalOutputDevice, PWMOutputDevice, factory)
            print("GPIO configurados em zero. Ligue SOMENTE a alimentacao dos motores.")
            print("Se alguma roda girar agora, desligue a alimentacao e cancele.")
            if input(f"Digite PULSO para testar {args.canal}: ").strip() != "PULSO":
                return
            # Limite adicional por sinal; nao substitui watchdog fisico.
            signal.setitimer(signal.ITIMER_REAL, 1.0)
            try:
                outputs.pulse(args.canal, args.inverter)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
            print("PULSO TERMINADO: habilitacoes e direcoes em zero.")
            input("Desligue a alimentacao dos motores; depois pressione Enter para sair. ")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            try:
                if outputs is not None:
                    outputs.close()
            finally:
                factory.close()
                for sig, handler in old.items():
                    signal.signal(sig, handler)
            print("Teste encerrado. Mantenha a alimentacao dos motores desligada.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--autoteste", action="store_true")
    mode.add_argument("--executar", action="store_true")
    parser.add_argument("--canal", choices=CHANNELS)
    parser.add_argument("--inverter", action="store_true")
    args = parser.parse_args()
    if args.autoteste:
        selftest()
    elif args.executar:
        physical_test(args)
    else:
        print("MAPA APENAS: nenhum GPIO sera acessado.")
        for name, pins in CHANNELS.items():
            print(name, "| GPIO IN1/IN2/EN:", pins,
                  "| pinos fisicos:", tuple(PHYSICAL[p] for p in pins))
        print("GND comum: pino fisico 6. Para teste sem hardware: --autoteste")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"TESTE INTERROMPIDO: {exc}", file=sys.stderr)
        sys.exit(1)
