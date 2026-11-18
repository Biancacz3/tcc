"""Local account administration. Run inside the API container; passwords prompt only."""
import argparse
import getpass
import sys

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionLocal
from app.models.user import User, UserSession
from app.passwords import hash_password, normalize_email


def password_prompt() -> str:
    password = getpass.getpass("Senha (12 a 128 caracteres): ")
    if password != getpass.getpass("Repita a senha: "):
        raise ValueError("As senhas nao coincidem.")
    return hash_password(password)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Criar conta individual")
    create.add_argument("--email")
    create.add_argument("--name")
    create.add_argument("--role", choices=("admin", "viewer"), default="admin")
    for command in ("reset-password", "disable"):
        child = commands.add_parser(command)
        child.add_argument("--email")
    commands.add_parser("list")
    args = parser.parse_args()
    try:
        with SessionLocal() as session:
            if args.command == "list":
                for user in session.scalars(select(User).order_by(User.email)):
                    print(f"{user.email} | {user.name} | {user.role} | {'ativa' if user.is_active else 'inativa'}")
                return 0
            email = normalize_email(args.email or input("E-mail: "))
            user = session.scalar(select(User).where(User.email == email).with_for_update())
            if args.command == "create":
                if user:
                    raise ValueError("Este e-mail ja esta cadastrado. Use reset-password se necessario.")
                name = (args.name or input("Nome: ")).strip()
                if not 1 <= len(name) <= 100:
                    raise ValueError("Nome deve ter entre 1 e 100 caracteres.")
                user = User(email=email, name=name, role=args.role, password_hash=password_prompt(), is_active=True)
                session.add(user)
            else:
                if not user:
                    raise ValueError("Conta nao encontrada.")
                if args.command == "reset-password":
                    user.password_hash = password_prompt()
                    user.is_active = True
                else:
                    user.is_active = False
                session.execute(delete(UserSession).where(UserSession.user_id == user.id))
            session.commit()
            print(f"Conta {email}: {args.command} concluido.")
        return 0
    except (ValueError, EOFError, KeyboardInterrupt) as exc:
        print(f"Nao concluido: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Nao concluido: confira se o banco esta ativo e as migracoes foram aplicadas.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
