#!/usr/bin/env python3
"""TCC: pulsos manuais com as quatro rodas, sem alterar o autonomo.

Requer testar_motores.py na mesma pasta. Sem argumentos: mostra o mapa.
--autoteste usa somente objetos falsos, sem importar bibliotecas GPIO.
--executar --movimento frente: pulso fisico unico, com confirmacao no terminal.
V3: --reducao-esquerda 2 reduz o lado esquerdo em 2 pontos percentuais,
somente no movimento frente. Padrao zero preserva os testes e curvas anteriores.
--potencia 45 permite PWM de 45%; padrao preservado em 35%.
Limite de ajuste: 20 a 50%. Nao e uma medida de potencia mecanica nem de tensao.
Primeiro teste: rodas suspensas. Nao instalar como servico nem usar em percurso
automatico. Tempo de pulso NAO representa distancia ou angulo calibrado.

Pinagem e polaridades confirmadas por Bianca em 08/10/2026:
frente-a = dianteira esquerda, normal; frente-b = dianteira direita, invertida;
tras-a = traseira esquerda, invertida; tras-b = traseira direita, normal.
PWM nao substitui tensao de alimentacao correta. A Pi deve ter fonte apropriada,
sem ligacao entre a saida 5 V do L298N e sua alimentacao independente.
O corte fisico dos motores continua necessario. finally/sinais nao protegem
contra travamento do sistema, SIGKILL ou perda de alimentacao.
"""
import argparse
import math
import signal
import sys
import time

from testar_motores import CHANNELS, Outputs, interrupted, process_lock

EXPECTED_PINS = {
    "frente-a": (17, 27, 12), "frente-b": (22, 23, 18),
    "tras-a": (5, 6, 20), "tras-b": (13, 19, 21),
}
WHEELS = {
    "frente-a": ("dianteira esquerda", "esquerda", False),
    "frente-b": ("dianteira direita", "direita", True),
    "tras-a": ("traseira esquerda", "esquerda", True),
    "tras-b": ("traseira direita", "direita", False),
}
MOVES = {
    "frente": {"esquerda": 1, "direita": 1},
    "re": {"esquerda": -1, "direita": -1},
    "esquerda": {"esquerda": -1, "direita": 1},
    "direita": {"esquerda": 1, "direita": -1},
}


def validate(move, duration, power=35, left_reduction=0):
    if CHANNELS != EXPECTED_PINS:
        raise ValueError("Pinagem diferente da confirmada. Nao iniciar.")
    if move not in MOVES:
        raise ValueError("Movimento invalido")
    if not math.isfinite(duration) or not 0.1 <= duration <= 0.5:
        raise ValueError("Tempo permitido: 0.1 a 0.5 segundo")
    if not math.isfinite(power) or not 20 <= power <= 50:
        raise ValueError("PWM permitido: 20 a 50 por cento")
    if not math.isfinite(left_reduction) or not 0 <= left_reduction <= 5:
        raise ValueError("Reducao esquerda permitida: 0 a 5 pontos percentuais")
    if left_reduction and move != "frente":
        raise ValueError("Reducao esquerda disponivel somente para frente")
    if power - left_reduction < 20:
        raise ValueError("PWM esquerdo deve ser pelo menos 20 por cento")


def pulse_all(outputs, move="frente", duration=0.3, wait=time.sleep, power=35,
              left_reduction=0):
    validate(move, duration, power, left_reduction)
    outputs.stop()
    try:
        # Configura todas as direcoes antes de habilitar qualquer motor.
        for channel, (_, side, inverted) in WHEELS.items():
            a, b, _ = CHANNELS[channel]
            reverse_electrical = inverted ^ (MOVES[move][side] < 0)
            outputs.inputs[b if reverse_electrical else a].on()
        for channel, (_, side, _) in WHEELS.items():
            enable_pin = CHANNELS[channel][2]
            duty = power - left_reduction if side == "esquerda" else power
            outputs.enables[enable_pin].value = duty / 100
        wait(duration)
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
    # Valores esperados independentes, conforme testes fisicos individuais.
    high_by_move = {
        "frente": {17, 23, 6, 13}, "re": {27, 22, 5, 19},
        "esquerda": {27, 23, 5, 13}, "direita": {17, 22, 6, 19},
    }
    for move, high in high_by_move.items():
        def inspect(seconds):
            assert seconds == 0.3
            assert {p for p, output in outputs.inputs.items() if output.value} == high
            assert all(output.value == 0.35 for output in outputs.enables.values())
        pulse_all(outputs, move, wait=inspect)
        assert all(pin.value == 0 for pin in pins.values())
    def inspect_adjusted(seconds):
        assert seconds == 0.3
        assert {p for p, output in outputs.inputs.items() if output.value} == {27, 23, 5, 13}
        assert all(output.value == 0.45 for output in outputs.enables.values())
    pulse_all(outputs, "esquerda", power=45, wait=inspect_adjusted)
    assert all(pin.value == 0 for pin in pins.values())
    def inspect_straight(seconds):
        assert seconds == 0.3
        assert {p for p, x in outputs.inputs.items() if x.value} == {17, 23, 6, 13}
        assert {p: x.value for p, x in outputs.enables.items()} == {
            12: 0.33, 18: 0.35, 20: 0.33, 21: 0.35}
    pulse_all(outputs, left_reduction=2, wait=inspect_straight)
    assert all(pin.value == 0 for pin in pins.values())
    for invalid in ({"left_reduction": -1}, {"left_reduction": 6},
                    {"left_reduction": float('nan')},
                    {"left_reduction": float('inf')},
                    {"left_reduction": 2, "move": "esquerda"},
                    {"left_reduction": 2, "move": "direita"},
                    {"left_reduction": 2, "move": "re"},
                    {"left_reduction": 2, "power": 20}):
        try:
            pulse_all(outputs, **invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Reducao invalida aceita")
        assert all(pin.value == 0 for pin in pins.values())
    for error in (KeyboardInterrupt, RuntimeError):
        def fail(seconds): raise error("falha simulada")
        try:
            pulse_all(outputs, power=35, left_reduction=2, wait=fail)
        except error:
            pass
        else:
            raise AssertionError("Falha nao propagada")
        assert all(pin.value == 0 for pin in pins.values())
    for seconds in (0, -1, 0.51, float('inf'), float('nan')):
        try:
            pulse_all(outputs, duration=seconds)
        except ValueError:
            pass
        else:
            raise AssertionError("Tempo invalido aceito")
    for power in (0, 19, 51, float('inf'), float('nan')):
        try:
            pulse_all(outputs, power=power)
        except ValueError:
            pass
        else:
            raise AssertionError("PWM invalido aceito")
    outputs.close()
    assert all(pin.closed for pin in pins.values())
    print("AUTOTESTE CONJUNTO V3 OK: ajuste por lado, polaridades e parada verificados.")
    print("Nenhum GPIO acessado. Movimentos reais ainda exigem teste fisico.")


def run_real(args):
    validate(args.movimento, args.tempo, args.potencia, args.reducao_esquerda)
    if not sys.stdin.isatty():
        raise RuntimeError("Use um terminal interativo; nao executar como servico")
    print(f"QUATRO RODAS V3: {args.movimento}, {args.tempo:.2f} s.")
    print(f"PWM esquerdo {args.potencia - args.reducao_esquerda:g}%; direito {args.potencia:g}%.")
    print("Primeiro teste com rodas suspensas, Pi na fonte e motores DESLIGADOS.")
    print("Confira GND comum, alimentacao adequada e habilitacoes sem jumpers de 5 V.")
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
            print("GPIO configurados em zero. Agora ligue a alimentacao dos motores.")
            print("Se alguma roda girar antes do pulso, desligue e cancele.")
            command = input("Digite PULSO para acionar as quatro rodas uma unica vez: ").strip()
            if command == "PULSO":
                signal.setitimer(signal.ITIMER_REAL, args.tempo + 0.7)
                try:
                    pulse_all(outputs, args.movimento, args.tempo, power=args.potencia,
                              left_reduction=args.reducao_esquerda)
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                print("PULSO CONCLUIDO: quatro habilitacoes e direcoes em zero.")
            else:
                print("Pulso cancelado; GPIO continuam em zero.")
            input("Desligue os motores; depois pressione Enter para encerrar. ")
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
    parser.add_argument("--movimento", choices=MOVES, default="frente")
    parser.add_argument("--tempo", type=float, default=0.3)
    parser.add_argument("--potencia", type=float, default=35,
                        help="Duty cycle PWM, de 20 a 50 por cento; padrao 35")
    parser.add_argument("--reducao-esquerda", type=float, default=0,
                        help="Somente frente: reduz PWM esquerdo de 0 a 5 pontos; padrao 0")
    args = parser.parse_args()
    if args.autoteste:
        selftest()
    elif args.executar:
        run_real(args)
    else:
        print("MAPA APENAS: nenhum GPIO acessado.")
        for channel, (wheel, side, inverted) in WHEELS.items():
            print(f"{channel}: {wheel}, inversao={inverted}")
        print("Use --autoteste para verificacao sem hardware.")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"TESTE INTERROMPIDO: {exc}", file=sys.stderr)
        sys.exit(1)
