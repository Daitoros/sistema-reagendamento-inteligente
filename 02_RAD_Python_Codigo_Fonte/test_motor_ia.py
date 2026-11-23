"""
Testes Unitários (pytest) — Sistema de Reagendamento Inteligente.

Valida rigorosamente as 5 Regras de Negócio (RN01 a RN05), os métodos das classes
de domínio e o fluxo principal completo do MRDS.
"""

from datetime import date, datetime, time, timedelta

import pytest

from database import DatabaseManager, popular_cenario_exemplo
from models import CompromissoFixo, Disciplina, SessaoEstudo, StatusSessao, Usuario
from motor_ia import MotorIA


# -----------------------------------------------------------------------------
# Fixtures auxiliares
# -----------------------------------------------------------------------------
@pytest.fixture
def motor() -> MotorIA:
    return MotorIA()


@pytest.fixture
def usuario_base() -> Usuario:
    return Usuario(
        nome="Davi Aires Medeiros",
        email="davi@4ads.edu.br",
        limiteHorasDiarias=3,
        turnosProdutivos=["MANHA", "TARDE", "NOITE"],
    )


# -----------------------------------------------------------------------------
# RN01 — Bloqueio de Conflitos
# -----------------------------------------------------------------------------
def test_rn01_bloqueio_conflito_compromisso_fixo_e_sessoes(motor: MotorIA, usuario_base: Usuario):
    """RN01: Nenhuma SessaoEstudo pode sobrepor CompromissoFixo ou outra SessaoEstudo."""
    dia = date(2026, 11, 24)  # Terça-feira
    aula = CompromissoFixo(
        usuario_id=usuario_base.id,
        titulo="Aula 4ADS",
        dataHoraInicio=datetime.combine(dia, time(19, 0)),
        dataHoraFim=datetime.combine(dia, time(22, 0)),
        recorrente=True,
    )
    usuario_base.adicionarCompromisso(aula)

    # Conflito direto com compromisso fixo
    conflito, motivo = motor.verificarConflitoAgenda(
        usuario_base,
        datetime.combine(dia, time(20, 0)),
        datetime.combine(dia, time(21, 0)),
    )
    assert conflito is True
    assert "Aula 4ADS" in motivo

    # Conflito na semana seguinte devido a recorrente=True
    prox_terca = dia + timedelta(days=7)
    assert aula.verificarConflito(
        datetime.combine(prox_terca, time(19, 30)),
        datetime.combine(prox_terca, time(20, 30)),
    ) is True

    # Não permite cadastrar compromisso fixo sobrepondo outro compromisso fixo (UC02)
    with pytest.raises(ValueError, match="RN01"):
        usuario_base.adicionarCompromisso(
            CompromissoFixo(
                usuario_id=usuario_base.id,
                titulo="Compromisso Conflitante",
                dataHoraInicio=datetime.combine(dia, time(21, 0)),
                dataHoraFim=datetime.combine(dia, time(23, 0)),
            )
        )

    # Conflito com outra sessão já agendada
    disc = Disciplina(
        usuario_id=usuario_base.id,
        nome="Banco de Dados",
        cargaHorariaTotal=4,
        dataLimite=date(2026, 11, 27),
        grauDificuldade=4,
    )
    usuario_base.disciplinas.append(disc)
    sessao_existente = SessaoEstudo(
        usuario_id=usuario_base.id,
        disciplina_id=disc.id,
        dataHoraInicio=datetime.combine(dia, time(14, 0)),
        dataHoraFim=datetime.combine(dia, time(16, 0)),
        status=StatusSessao.AGENDADO,
    )
    usuario_base.sessoes.append(sessao_existente)

    conflito_sessao, _ = motor.verificarConflitoAgenda(
        usuario_base,
        datetime.combine(dia, time(15, 0)),
        datetime.combine(dia, time(17, 0)),
    )
    assert conflito_sessao is True

    # Janelas livres nunca podem sobrepor o compromisso fixo (19h-22h) nem a sessão (14h-16h)
    janelas = motor.buscarJanelasLivres(
        usuario_base,
        deadline=dia,
        data_referencia=datetime.combine(dia, time(8, 0)),
    )
    for j in janelas:
        assert not aula.verificarConflito(j["inicio"], j["fim"])
        assert not sessao_existente.verificarConflito(j["inicio"], j["fim"])


# -----------------------------------------------------------------------------
# RN02 — Gatilho de Reagendamento (Manual e Automático)
# -----------------------------------------------------------------------------
def test_rn02_gatilho_manual_e_automatico(motor: MotorIA, usuario_base: Usuario):
    """RN02: Aciona reagendamento manualmente ('Tive um imprevisto') e automaticamente ao vencer."""
    terca = date(2026, 11, 24)
    sexta = date(2026, 11, 27)
    disc = Disciplina(
        usuario_id=usuario_base.id,
        nome="Banco de Dados",
        cargaHorariaTotal=4,
        dataLimite=sexta,
        grauDificuldade=4,
    )
    usuario_base.disciplinas.append(disc)

    # 1. Gatilho manual (UC07)
    sessao_manual = SessaoEstudo(
        usuario_id=usuario_base.id,
        disciplina_id=disc.id,
        dataHoraInicio=datetime.combine(terca, time(14, 0)),
        dataHoraFim=datetime.combine(terca, time(16, 0)),
        status=StatusSessao.AGENDADO,
    )
    usuario_base.sessoes.append(sessao_manual)

    usuario_base.reportarImprevisto(sessao_manual)
    assert sessao_manual.status == StatusSessao.ATRASADO

    rel_manual = motor.acionarReagendamento(
        usuario_base, sessao_manual, data_referencia=datetime.combine(terca, time(16, 0))
    )
    assert rel_manual["sucesso"] is True
    assert len(rel_manual["novas_sessoes"]) >= 1
    assert rel_manual["notificacao"]["titulo"]

    # 2. Gatilho automático quando horário de término passa sem CONCLUIDO
    sessao_vencida = SessaoEstudo(
        usuario_id=usuario_base.id,
        disciplina_id=disc.id,
        dataHoraInicio=datetime.combine(terca, time(9, 0)),
        dataHoraFim=datetime.combine(terca, time(10, 0)),
        status=StatusSessao.AGENDADO,
    )
    usuario_base.sessoes.append(sessao_vencida)

    relatorios_auto = motor.verificarSessoesVencidas(
        usuario_base, agora=datetime.combine(terca, time(11, 0))
    )
    assert len(relatorios_auto) == 1
    assert sessao_vencida.status == StatusSessao.ATRASADO
    assert relatorios_auto[0]["sucesso"] is True


# -----------------------------------------------------------------------------
# RN03 — Priorização por Prazo (Deadline) e Desempate por Dificuldade
# -----------------------------------------------------------------------------
def test_rn03_priorizacao_por_deadline_e_dificuldade(motor: MotorIA, usuario_base: Usuario):
    """RN03: Prioriza menor dataLimite; em caso de empate, maior grauDificuldade vence."""
    d_longe = Disciplina(
        usuario_id=usuario_base.id,
        nome="Redes",
        cargaHorariaTotal=4,
        dataLimite=date(2026, 12, 10),
        grauDificuldade=5,
    )
    d_proximo_facil = Disciplina(
        usuario_id=usuario_base.id,
        nome="Ética",
        cargaHorariaTotal=2,
        dataLimite=date(2026, 11, 27),
        grauDificuldade=2,
    )
    d_proximo_dificil = Disciplina(
        usuario_id=usuario_base.id,
        nome="Banco de Dados",
        cargaHorariaTotal=4,
        dataLimite=date(2026, 11, 27),
        grauDificuldade=5,
    )

    ordenadas = motor.priorizarDisciplinas([d_longe, d_proximo_facil, d_proximo_dificil])
    assert [d.nome for d in ordenadas] == ["Banco de Dados", "Ética", "Redes"]


# -----------------------------------------------------------------------------
# RN04 — Fragmentação de Blocos
# -----------------------------------------------------------------------------
def test_rn04_fragmentacao_blocos_quando_sem_janela_continua(motor: MotorIA, usuario_base: Usuario):
    """RN04: Fragmenta sessão de 2h em dois blocos de 1h em dias diferentes quando não há 2h contínuas."""
    terca = date(2026, 11, 24)
    quarta = date(2026, 11, 25)
    quinta = date(2026, 11, 26)
    sexta = date(2026, 11, 27)

    # Limita turnos a TARDE (13h-18h) para controlar exatamente as janelas
    usuario_base.turnosProdutivos = ["TARDE"]
    usuario_base.limiteHorasDiarias = 3

    disc = Disciplina(
        usuario_id=usuario_base.id,
        nome="Banco de Dados",
        cargaHorariaTotal=4,
        dataLimite=sexta,
        grauDificuldade=4,
    )
    usuario_base.disciplinas.append(disc)

    # Quarta-feira ocupada de 13h às 17h -> sobra apenas 1h (17h-18h)
    usuario_base.adicionarCompromisso(
        CompromissoFixo(
            usuario_id=usuario_base.id,
            titulo="Reunião Quarta",
            dataHoraInicio=datetime.combine(quarta, time(13, 0)),
            dataHoraFim=datetime.combine(quarta, time(17, 0)),
        )
    )
    # Quinta-feira já tem 2h de estudo agendadas -> com limite 3h/dia, sobra só 1h de cota!
    sessao_quinta = SessaoEstudo(
        usuario_id=usuario_base.id,
        disciplina_id=disc.id,
        dataHoraInicio=datetime.combine(quinta, time(14, 0)),
        dataHoraFim=datetime.combine(quinta, time(16, 0)),
        status=StatusSessao.AGENDADO,
    )
    usuario_base.sessoes.append(sessao_quinta)

    # Terça-feira (16h-18h) perdida por imprevisto -> 2h para reagendar
    sessao_terca = SessaoEstudo(
        usuario_id=usuario_base.id,
        disciplina_id=disc.id,
        dataHoraInicio=datetime.combine(terca, time(16, 0)),
        dataHoraFim=datetime.combine(terca, time(18, 0)),
        status=StatusSessao.AGENDADO,
    )
    usuario_base.sessoes.append(sessao_terca)

    # Sexta-feira totalmente ocupada à tarde (13h-18h)
    usuario_base.adicionarCompromisso(
        CompromissoFixo(
            usuario_id=usuario_base.id,
            titulo="Apresentação Sexta",
            dataHoraInicio=datetime.combine(sexta, time(13, 0)),
            dataHoraFim=datetime.combine(sexta, time(18, 0)),
        )
    )

    usuario_base.reportarImprevisto(sessao_terca)
    relatorio = motor.acionarReagendamento(
        usuario_base, sessao_terca, data_referencia=datetime.combine(terca, time(18, 0))
    )

    assert relatorio["sucesso"] is True
    assert relatorio["fragmentada"] is True
    assert len(relatorio["novas_sessoes"]) == 2
    assert sum(b.obterDuracao() for b in relatorio["novas_sessoes"]) == 120
    assert relatorio["novas_sessoes"][0].dataHoraInicio.date() == quarta
    assert relatorio["novas_sessoes"][1].dataHoraInicio.date() == quinta


# -----------------------------------------------------------------------------
# RN05 — Limite de Carga Diária (Prevenção de Burnout)
# -----------------------------------------------------------------------------
def test_rn05_respeito_rigoroso_ao_limite_diario(motor: MotorIA, usuario_base: Usuario):
    """RN05: Nunca reagenda sessões ultrapassando limiteHorasDiarias do estudante."""
    quarta = date(2026, 11, 25)
    quinta = date(2026, 11, 26)
    usuario_base.limiteHorasDiarias = 2

    disc = Disciplina(
        usuario_id=usuario_base.id,
        nome="Algoritmos",
        cargaHorariaTotal=4,
        dataLimite=quinta,
        grauDificuldade=5,
    )
    usuario_base.disciplinas.append(disc)

    # Quarta já atingiu 2h/2h
    usuario_base.sessoes.append(
        SessaoEstudo(
            usuario_id=usuario_base.id,
            disciplina_id=disc.id,
            dataHoraInicio=datetime.combine(quarta, time(8, 0)),
            dataHoraFim=datetime.combine(quarta, time(10, 0)),
            status=StatusSessao.AGENDADO,
        )
    )

    assert motor.validarLimiteDiario(usuario_base, quarta, 1.0) is False
    assert motor.validarLimiteDiario(usuario_base, quinta, 2.0) is True
    assert motor.validarLimiteDiario(usuario_base, quinta, 2.5) is False

    # Sessão atrasada de 2h na quarta deve ir obrigatoriamente para quinta (jamais quarta)
    sessao_atrasada = SessaoEstudo(
        usuario_id=usuario_base.id,
        disciplina_id=disc.id,
        dataHoraInicio=datetime.combine(quarta, time(10, 0)),
        dataHoraFim=datetime.combine(quarta, time(12, 0)),
        status=StatusSessao.ATRASADO,
    )
    usuario_base.sessoes.append(sessao_atrasada)

    rel = motor.acionarReagendamento(
        usuario_base, sessao_atrasada, data_referencia=datetime.combine(quarta, time(12, 0))
    )
    assert rel["sucesso"] is True
    for bloco in rel["novas_sessoes"]:
        assert bloco.dataHoraInicio.date() == quinta
    assert motor.calcular_horas_alocadas_no_dia(usuario_base, quarta) <= usuario_base.limiteHorasDiarias
    assert motor.calcular_horas_alocadas_no_dia(usuario_base, quinta) <= usuario_base.limiteHorasDiarias


# -----------------------------------------------------------------------------
# Fluxo Principal Integrado (Seed SQLite + MotorIA + Disciplina.calcularHorasPendentes)
# -----------------------------------------------------------------------------
def test_fluxo_principal_seed_e_persistencia(tmp_path):
    """Valida o cenário completo do Seed do PDF com persistência real em SQLite."""
    db = DatabaseManager(tmp_path / "teste_reagendamento.db")
    motor = MotorIA()

    usuario = popular_cenario_exemplo(db)
    assert usuario.nome == "Davi Aires Medeiros"
    assert len(usuario.disciplinas) == 2

    disc_bd = next(d for d in usuario.disciplinas if d.nome == "Banco de Dados")
    assert disc_bd.calcularHorasPendentes() == 4

    # Pega a sessão de terça-feira (primeira sessão cronológica)
    sessao_terca = sorted(usuario.sessoes, key=lambda s: s.dataHoraInicio)[0]
    assert sessao_terca.obterDuracao() == 120

    # Reporta imprevisto na terça-feira -> deve fragmentar em Quarta (1h) + Quinta (1h)
    usuario.reportarImprevisto(sessao_terca)
    rel = motor.acionarReagendamento(
        usuario, sessao_terca, data_referencia=sessao_terca.dataHoraFim
    )

    assert rel["sucesso"] is True
    assert rel["fragmentada"] is True
    assert len(rel["novas_sessoes"]) == 2
    assert [b.obterDuracao() for b in rel["novas_sessoes"]] == [60, 60]

    # Persiste e conclui um bloco para validar calcularHorasPendentes()
    for b in rel["novas_sessoes"]:
        db.salvar_sessao(b)
    db.atualizar_status_sessao(sessao_terca.id, StatusSessao.ATRASADO)
    usuario.concluirSessao(rel["novas_sessoes"][0])
    db.atualizar_status_sessao(rel["novas_sessoes"][0].id, StatusSessao.CONCLUIDO)

    usuario_recarregado = db.carregar_usuario_completo(usuario.id)
    assert usuario_recarregado is not None
    disc_bd_rec = next(d for d in usuario_recarregado.disciplinas if d.nome == "Banco de Dados")
    assert disc_bd_rec.calcularHorasPendentes() == 3
