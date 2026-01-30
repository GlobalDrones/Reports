import sys
import os
import argparse
from pathlib import Path

sys.path.append(os.getcwd())

try:
    from app.config import get_settings
    from app.db import run_migrations, _db_path
except ImportError:
    sys.path.append(str(Path(__file__).parent.parent))
    from app.config import get_settings
    from app.db import run_migrations, _db_path

def clean_db(yes: bool = False):
    settings = get_settings()
    db_path = _db_path(settings)

    if db_path.exists():
        if not yes:
            confirm = input(f"Isso irá apagar COMPLETAMENTE o banco de dados em '{db_path}'.\nTem certeza? [y/N] ")
            if confirm.lower() != 'y':
                print("Operação cancelada.")
                return

        print(f"Removendo arquivo do banco de dados: {db_path}")
        try:
            db_path.unlink()
            print("Arquivo removido com sucesso.")
        except Exception as e:
            print(f"Erro ao remover arquivo: {e}")
            return
    else:
        print(f"Arquivo de banco de dados não encontrado: {db_path}")

    print("Recriando esquema do banco de dados (migrações)...")
    try:
        run_migrations(settings)
        print("Banco de dados recriado com sucesso!")
    except Exception as e:
        print(f"Erro ao rodar migrações: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Limpa o banco de dados SQLite e recria o schema.")
    parser.add_argument("--yes", action="store_true", help="Confirma automaticamente sem perguntar.")
    
    args = parser.parse_args()
    clean_db(args.yes)
