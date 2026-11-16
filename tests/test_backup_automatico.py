"""Backup automático do banco (requisito 19.6.13): frequência, retenção, registro e cópias antes de operações."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from eco import backup


@pytest.fixture
def banco(tmp_path):
    arquivo = tmp_path / "eco.db"
    con = sqlite3.connect(str(arquivo))
    con.executescript("CREATE TABLE ideias (id TEXT PRIMARY KEY, nome TEXT); INSERT INTO ideias VALUES ('1', 'Python');")
    con.commit()
    con.close()
    return arquivo


def test_so_faz_quando_passou_a_frequencia(banco, tmp_path):
    pasta = tmp_path / "backups"
    t0 = datetime(2026, 11, 12, 8, 0)
    primeira = backup.backup_automatico(banco, pasta, horas=24, agora=t0)
    assert primeira is not None and primeira.name == "eco-20261112-080000-automatico.db"
    assert backup.backup_automatico(banco, pasta, horas=24, agora=t0 + timedelta(hours=23)) is None
    assert backup.backup_automatico(banco, pasta, horas=24, agora=t0 + timedelta(hours=25)) is not None
    regs = backup.registros(pasta)
    assert [r["motivo"] for r in regs] == ["automatico", "automatico"] and all(r["ok"] for r in regs)
    assert regs[0]["tabelas"] == {"ideias": 1}


def test_guarda_so_as_automaticas_mais_novas_e_nunca_apaga_as_outras(banco, tmp_path):
    pasta = tmp_path / "backups"
    manual = backup.fazer_backup(banco, pasta, "manual", datetime(2026, 1, 1))
    antes = backup.antes_de("importar grafo", banco, pasta)
    t = datetime(2026, 11, 1)
    for i in range(5):
        backup.backup_automatico(banco, pasta, horas=24, manter=3, agora=t + timedelta(days=i))
    automaticas = sorted(a.name for a in backup.backups(pasta) if "automatico" in a.name)
    assert automaticas == ["eco-20261103-000000-automatico.db", "eco-20261104-000000-automatico.db", "eco-20261105-000000-automatico.db"]
    assert manual.exists() and antes.exists() and "antes-importar-grafo" in antes.name


def test_desligado_ou_sem_banco_nao_faz_nada(tmp_path):
    assert backup.backup_automatico(tmp_path / "nao-existe.db", tmp_path / "b", horas=24) is None
    assert backup.backup_automatico(tmp_path / "nao-existe.db", tmp_path / "b", horas=0) is None


def test_falha_fica_registrada(banco, tmp_path, monkeypatch):
    pasta = tmp_path / "backups"

    def quebra(*a, **k):
        raise OSError("disco cheio")

    monkeypatch.setattr(backup, "fazer_backup", quebra)
    with pytest.raises(OSError):
        backup.backup_automatico(banco, pasta, horas=1, agora=datetime(2026, 11, 12))
    r = backup.registros(pasta)[-1]
    assert not r["ok"] and "disco cheio" in r["erro"]


def test_registro_pelo_comando(banco, tmp_path, monkeypatch, capsys):
    pasta = tmp_path / "backups"
    backup.backup_automatico(banco, pasta, horas=1, agora=datetime(2026, 11, 12, 9))
    original = backup.registros
    monkeypatch.setattr(backup, "registros", lambda: original(pasta))
    assert backup.comando_backup(["registro"]) == 0
    assert "2026-11-12T09:00:00  automatico" in capsys.readouterr().out


def test_thread_do_backup_automatico(tmp_path, monkeypatch):
    from eco import principal

    feitas = []
    monkeypatch.setattr(backup, "backup_automatico", lambda horas, manter: feitas.append((horas, manter)) or None)
    cfg = type("Cfg", (), {"backup_horas": 12.0, "backup_manter": 5})()
    t = principal.iniciar_backup_automatico(cfg, dormir=lambda s: False)
    t.join(5)
    assert feitas == [(12.0, 5)]
    cfg.backup_horas = 0
    assert principal.iniciar_backup_automatico(cfg) is None
