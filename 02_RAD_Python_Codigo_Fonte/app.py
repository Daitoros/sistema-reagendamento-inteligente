"""
Interface RAD em Streamlit — Sistema de Reagendamento Inteligente.

Implementa todos os 12 Casos de Uso (UC01 a UC12) e as 5 Regras de Negócio (RN01 a RN05)
com persistência real em SQLite e visualização interativa da agenda e do Motor de IA.

Execução:
    streamlit run app.py
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional
from uuid import UUID

import pandas as pd
import streamlit as st

from database import DatabaseManager, popular_cenario_exemplo
from models import CompromissoFixo, Disciplina, SessaoEstudo, StatusSessao, Usuario
from motor_ia import MotorIA

# -----------------------------------------------------------------------------
# Configuração da Página e Estilos Visuais
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Sistema de Reagendamento Inteligente",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
.card-evento {
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 10px;
    border-left: 6px solid #64748b;
    background-color: #f8fafc;
    color: #0f172a;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
}
.card-fixo {
    border-left-color: #475569;
    background-color: #f1f5f9;
}
.card-agendado {
    border-left-color: #2563eb;
    background-color: #eff6ff;
}
.card-concluido {
    border-left-color: #16a34a;
    background-color: #f0fdf4;
}
.card-atrasado {
    border-left-color: #dc2626;
    background-color: #fef2f2;
}
.card-cancelado {
    border-left-color: #94a3b8;
    background-color: #f8fafc;
    opacity: 0.78;
}
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.02em;
}
.badge-fixo { background: #cbd5e1; color: #1e293b; }
.badge-agendado { background: #bfdbfe; color: #1d4ed8; }
.badge-concluido { background: #bbf7d0; color: #15803d; }
.badge-atrasado { background: #fecaca; color: #b91c1c; }
.badge-cancelado { background: #e2e8f0; color: #475569; }
.badge-frag { background: #fef08a; color: #854d0e; margin-left: 6px; }
.evento-titulo {
    font-weight: 700;
    font-size: 0.98rem;
    margin-top: 4px;
}
.evento-meta {
    font-size: 0.84rem;
    color: #334155;
    margin-top: 2px;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

DIAS_SEMANA_PT = [
    "Segunda-feira",
    "Terça-feira",
    "Quarta-feira",
    "Quinta-feira",
    "Sexta-feira",
    "Sábado",
    "Domingo",
]


# -----------------------------------------------------------------------------
# Inicialização de Recursos e Estado de Sessão
# -----------------------------------------------------------------------------
@st.cache_resource
def obter_db() -> DatabaseManager:
    return DatabaseManager()


@st.cache_resource
def obter_motor_ia() -> MotorIA:
    return MotorIA()


db = obter_db()
motor = obter_motor_ia()

if "usuario_ativo_id" not in st.session_state:
    st.session_state["usuario_ativo_id"] = None
if "ultimo_relatorio_ia" not in st.session_state:
    st.session_state["ultimo_relatorio_ia"] = None
if "confirmar_acao" not in st.session_state:
    st.session_state["confirmar_acao"] = None  # tuple (tipo, sessao_id)


# -----------------------------------------------------------------------------
# Helpers de Persistência de Reagendamento
# -----------------------------------------------------------------------------
def persistir_relatorio_reagendamento(
    usuario: Usuario, sessao_original: SessaoEstudo, relatorio: dict
) -> None:
    """Salva no SQLite o novo status da sessão original, os novos blocos e a notificação (UC12)."""
    db.atualizar_status_sessao(sessao_original.id, sessao_original.status)
    for nova_sessao in relatorio["novas_sessoes"]:
        db.salvar_sessao(nova_sessao)

    notif = relatorio["notificacao"]
    db.salvar_notificacao(
        usuario_id=usuario.id,
        sessao_id=sessao_original.id,
        titulo=notif["titulo"],
        mensagem=notif["mensagem"],
        detalhes={
            "fragmentada": relatorio["fragmentada"],
            "sucesso": relatorio["sucesso"],
            "parcial": relatorio["parcial"],
            "prioridade_posicao": relatorio["prioridade_posicao"],
            "ranking_prioridades": relatorio["ranking_prioridades"],
            "blocos": notif["blocos"],
            "log": relatorio["log"],
        },
    )


def executar_verificacao_automatica_rn02(usuario: Usuario) -> List[dict]:
    """
    Executa a verificação automática da RN02 ao carregar a página:
    sessões AGENDADO cujo término já passou são marcadas como ATRASADO e reagendadas.
    """
    agora = datetime.now()
    sessoes_antes = {s.id: s for s in usuario.sessoes}
    relatorios = motor.verificarSessoesVencidas(usuario, agora=agora)

    for rel in relatorios:
        sessao_orig = sessoes_antes.get(UUID(rel["sessao_original_id"]))
        if sessao_orig is not None:
            persistir_relatorio_reagendamento(usuario, sessao_orig, rel)

    return relatorios


def exibir_banner_relatorio_ia(relatorio: dict) -> None:
    """Renderiza um alerta rico detalhando a decisão do MotorIA (UC08 a UC12)."""
    notif = relatorio["notificacao"]
    if relatorio["sucesso"]:
        st.success(f"🤖 **{notif['titulo']}**\n\n{notif['mensagem']}")
    elif relatorio["parcial"]:
        st.warning(f"⚠️ **{notif['titulo']}**\n\n{notif['mensagem']}")
    else:
        st.error(f"❌ **{notif['titulo']}**\n\n{notif['mensagem']}")

    with st.expander("🔍 Ver detalhes da análise do Motor de IA (RN01 a RN05)", expanded=True):
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Blocos Redistribuídos:**")
            blocos = notif.get("blocos", [])
            if blocos:
                df_blocos = pd.DataFrame(
                    [
                        {
                            "Dia": f"{b['dia_semana']} ({b['data']})",
                            "Início": b["inicio"],
                            "Fim": b["fim"],
                            "Duração": f"{b['duracao_horas']:.1f}h",
                        }
                        for b in blocos
                    ]
                )
                st.dataframe(df_blocos, use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum bloco pôde ser alocado dentro do deadline e limites atuais.")

        with col_b:
            st.markdown("**Ranking de Prioridade (RN03 — UC11):**")
            for item in relatorio.get("ranking_prioridades", []):
                st.markdown(f"- {item}")

        st.markdown("**Log de Decisão do Motor de IA:**")
        for linha in relatorio.get("log", []):
            st.markdown(f"- `{linha}`")


# -----------------------------------------------------------------------------
# Sidebar — Seleção de Perfil, Seed e Ações Rápidas
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("🧠 Reagendamento IA")
    st.caption("Assistente Autônomo de Gestão de Tempo — 4ADS")

    st.divider()
    st.subheader("🎯 Teste Rápido (Fluxo Principal)")
    if st.button(
        "🚀 Carregar Cenário de Exemplo",
        use_container_width=True,
        type="primary",
        help="Popula o banco exatamente com o fluxo do MRDS: Banco de Dados (4h até sexta) com blocos na terça e quinta.",
    ):
        usuario_seed = popular_cenario_exemplo(db)
        st.session_state["usuario_ativo_id"] = str(usuario_seed.id)
        st.session_state["ultimo_relatorio_ia"] = None
        st.session_state["confirmar_acao"] = None
        st.toast("Cenário de exemplo carregado! Teste 'Tive um imprevisto' na terça-feira.", icon="🚀")
        st.rerun()

    usuarios_cadastrados = db.listar_usuarios()

    if usuarios_cadastrados:
        ids_disponiveis = [str(u.id) for u in usuarios_cadastrados]
        if st.session_state["usuario_ativo_id"] not in ids_disponiveis:
            st.session_state["usuario_ativo_id"] = ids_disponiveis[0]

        mapa_labels = {str(u.id): f"{u.nome} ({u.limiteHorasDiarias}h/dia)" for u in usuarios_cadastrados}
        idx_atual = ids_disponiveis.index(st.session_state["usuario_ativo_id"])
        selecionado = st.selectbox(
            "Perfil Ativo (UC01)",
            options=ids_disponiveis,
            index=idx_atual,
            format_func=lambda uid: mapa_labels[uid],
        )
        if selecionado != st.session_state["usuario_ativo_id"]:
            st.session_state["usuario_ativo_id"] = selecionado
            st.session_state["ultimo_relatorio_ia"] = None
            st.rerun()

        usuario_ativo = db.carregar_usuario_completo(st.session_state["usuario_ativo_id"])
    else:
        usuario_ativo = None

    if usuario_ativo is not None:
        st.divider()
        st.markdown(f"**Estudante:** {usuario_ativo.nome}")
        st.markdown(f"**Limite Diário (RN05):** `{usuario_ativo.limiteHorasDiarias}h/dia`")
        st.markdown(f"**Turnos Produtivos:** `{', '.join(usuario_ativo.turnosProdutivos)}`")

        notifs_nao_lidas = [n for n in db.listar_notificacoes(usuario_ativo.id) if not n["lida"]]
        st.markdown(f"**Notificações não lidas (UC12):** `{len(notifs_nao_lidas)}`")

        st.divider()
        if st.button(
            "⏱️ Simular Sessão Vencida (RN02)",
            use_container_width=True,
            help="Cria uma sessão agendada no passado recente para demonstrar o gatilho automático da RN02 ao recarregar.",
        ):
            if usuario_ativo.disciplinas:
                disc_alvo = motor.priorizarDisciplinas(usuario_ativo.disciplinas)[0]
                fim_passado = (datetime.now() - timedelta(minutes=30)).replace(second=0, microsecond=0)
                ini_passado = fim_passado - timedelta(hours=1)
                sessao_vencida = SessaoEstudo(
                    usuario_id=usuario_ativo.id,
                    disciplina_id=disc_alvo.id,
                    dataHoraInicio=ini_passado,
                    dataHoraFim=fim_passado,
                    status=StatusSessao.AGENDADO,
                )
                db.salvar_sessao(sessao_vencida)
                st.toast("Sessão vencida inserida! Acionando gatilho automático RN02...", icon="⏱️")
                st.rerun()
            else:
                st.warning("Cadastre uma disciplina primeiro (UC03).")

    st.divider()
    with st.expander("📋 Regras de Negócio Ativas"):
        st.markdown(
            """
            - **RN01:** Bloqueio de Conflitos
            - **RN02:** Gatilho Automático e Manual
            - **RN03:** Priorização por Deadline
            - **RN04:** Fragmentação de Blocos
            - **RN05:** Limite de Carga Diária
            """
        )


# -----------------------------------------------------------------------------
# Cabeçalho Principal e Verificação Automática RN02
# -----------------------------------------------------------------------------
st.title("🧠 Sistema de Reagendamento Inteligente")
st.caption(
    "Assistente autônomo de gestão de tempo para estudantes • "
    "Autores: Davi Aires Bastos Medeiros e Yuri da Silva Araújo"
)

if usuario_ativo is not None:
    relatorios_auto = executar_verificacao_automatica_rn02(usuario_ativo)
    if relatorios_auto:
        # Recarrega o usuário após persistência automática
        usuario_ativo = db.carregar_usuario_completo(usuario_ativo.id)
        st.session_state["ultimo_relatorio_ia"] = relatorios_auto[-1]
        st.info(
            f"⏱️ **Gatilho Automático (RN02):** {len(relatorios_auto)} sessão(ões) vencida(s) "
            f"detectada(s) e processada(s) automaticamente pelo Motor de IA!"
        )

# Caso ainda não exista nenhum perfil cadastrado, orienta cadastro (UC01) ou carga do Seed
if usuario_ativo is None:
    st.info(
        "👋 Bem-vindo! Ainda não há nenhum perfil de estudante cadastrado. "
        "Clique em **🚀 Carregar Cenário de Exemplo** na barra lateral para testar imediatamente "
        "o Fluxo Principal do MRDS ou cadastre seu perfil abaixo (**UC01**)."
    )
    with st.form("form_primeiro_perfil"):
        st.subheader("UC01 — Cadastrar Perfil do Estudante")
        col1, col2 = st.columns(2)
        nome_ini = col1.text_input("Nome completo *", placeholder="Ex.: Davi Aires Medeiros")
        email_ini = col2.text_input("E-mail *", placeholder="Ex.: estudante@4ads.edu.br")
        col3, col4 = st.columns(2)
        limite_ini = col3.slider("Limite máximo de horas de estudo/dia (RN05)", 1, 12, 4)
        turnos_ini = col4.multiselect(
            "Turnos de maior produtividade",
            options=["MANHA", "TARDE", "NOITE"],
            default=["MANHA", "TARDE", "NOITE"],
        )
        if st.form_submit_button("Cadastrar Perfil", type="primary"):
            try:
                novo = Usuario(
                    nome=nome_ini,
                    email=email_ini,
                    limiteHorasDiarias=limite_ini,
                    turnosProdutivos=turnos_ini or ["MANHA", "TARDE", "NOITE"],
                )
                db.salvar_usuario(novo)
                st.session_state["usuario_ativo_id"] = str(novo.id)
                st.success("Perfil cadastrado com sucesso!")
                st.rerun()
            except Exception as exc:
                st.error(f"Erro ao cadastrar perfil: {exc}")
    st.stop()


# -----------------------------------------------------------------------------
# Abas Principais da Aplicação (UC01 a UC12)
# -----------------------------------------------------------------------------
mapa_disciplinas: Dict[UUID, Disciplina] = {d.id: d for d in usuario_ativo.disciplinas}

tab_agenda, tab_agendar, tab_disciplinas, tab_compromissos, tab_perfil, tab_notificacoes = st.tabs(
    [
        "📅 Agenda & IA (UC04, UC06–UC12)",
        "➕ Agendar Sessão (UC05)",
        "📚 Disciplinas / Metas (UC03, UC11)",
        "🔒 Compromissos Fixos (UC02)",
        "👤 Perfil (UC01)",
        "🔔 Notificações (UC12)",
    ]
)


# =============================================================================
# ABA 1: AGENDA VISUAL & AÇÕES (UC04, UC06, UC07, UC08–UC12)
# =============================================================================
with tab_agenda:
    # KPIs principais
    sessoes_agendadas = [s for s in usuario_ativo.sessoes if s.status == StatusSessao.AGENDADO]
    sessoes_concluidas = [s for s in usuario_ativo.sessoes if s.status == StatusSessao.CONCLUIDO]
    sessoes_atrasadas = [s for s in usuario_ativo.sessoes if s.status == StatusSessao.ATRASADO]

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Sessões Agendadas", len(sessoes_agendadas))
    k2.metric("Concluídas", len(sessoes_concluidas))
    k3.metric("Atrasadas (Imprevistos)", len(sessoes_atrasadas))
    k4.metric("Compromissos Fixos", len(usuario_ativo.compromissos))
    k5.metric("Limite Diário (RN05)", f"{usuario_ativo.limiteHorasDiarias}h/dia")

    # Exibe o relatório detalhado do último reagendamento feito pela IA
    if st.session_state.get("ultimo_relatorio_ia") is not None:
        exibir_banner_relatorio_ia(st.session_state["ultimo_relatorio_ia"])
        if st.button("Fechar relatório da IA"):
            st.session_state["ultimo_relatorio_ia"] = None
            st.rerun()

    st.subheader("UC04 — Consulta de Agenda Semanal / Cronológica")

    # Determina intervalo padrão com base nos eventos existentes
    todas_datas: List[date] = (
        [c.dataHoraInicio.date() for c in usuario_ativo.compromissos]
        + [s.dataHoraInicio.date() for s in usuario_ativo.sessoes]
    )
    data_min_padrao = min(todas_datas) if todas_datas else date.today()
    data_max_padrao = max(todas_datas) if todas_datas else date.today() + timedelta(days=6)

    f1, f2, f3 = st.columns([1.2, 1.2, 1.6])
    data_inicio_filtro = f1.date_input("De:", value=data_min_padrao, key="filtro_ini")
    data_fim_filtro = f2.date_input("Até:", value=data_max_padrao, key="filtro_fim")
    filtro_status = f3.multiselect(
        "Filtrar status das sessões:",
        options=[s.value for s in StatusSessao],
        default=[StatusSessao.AGENDADO.value, StatusSessao.CONCLUIDO.value, StatusSessao.ATRASADO.value],
    )

    if data_fim_filtro < data_inicio_filtro:
        st.warning("A data final deve ser maior ou igual à data inicial.")
    else:
        # Monta lista unificada de eventos no período
        eventos_por_dia: Dict[date, List[dict]] = {}
        dia_cursor = data_inicio_filtro
        while dia_cursor <= data_fim_filtro:
            eventos_dia: List[dict] = []

            for comp in usuario_ativo.compromissos:
                oc = comp.obter_ocorrencia_no_dia(dia_cursor)
                if oc is not None:
                    eventos_dia.append(
                        {
                            "tipo": "FIXO",
                            "inicio": oc[0],
                            "fim": oc[1],
                            "titulo": comp.titulo,
                            "objeto": comp,
                        }
                    )

            for sessao in usuario_ativo.sessoes:
                if (
                    sessao.dataHoraInicio.date() == dia_cursor
                    and sessao.status.value in filtro_status
                ):
                    disc = mapa_disciplinas.get(sessao.disciplina_id)
                    eventos_dia.append(
                        {
                            "tipo": "SESSAO",
                            "inicio": sessao.dataHoraInicio,
                            "fim": sessao.dataHoraFim,
                            "titulo": disc.nome if disc else "Disciplina",
                            "disciplina": disc,
                            "objeto": sessao,
                        }
                    )

            if eventos_dia:
                eventos_dia.sort(key=lambda e: e["inicio"])
                eventos_por_dia[dia_cursor] = eventos_dia

            dia_cursor += timedelta(days=1)

        if not eventos_por_dia:
            # Fluxo alternativo de UC04
            st.info("Não há compromissos fixos ou sessões de estudo registradas para o período selecionado.")
        else:
            st.markdown(
                "**Legenda:** "
                "<span class='badge badge-fixo'>🔒 COMPROMISSO FIXO</span> "
                "<span class='badge badge-agendado'>📘 AGENDADO</span> "
                "<span class='badge badge-concluido'>✅ CONCLUÍDO</span> "
                "<span class='badge badge-atrasado'>⚠️ ATRASADO</span> "
                "<span class='badge badge-cancelado'>✖ CANCELADO</span>",
                unsafe_allow_html=True,
            )
            st.write("")

            # Renderização agrupada por dia (visão cronológica clara e responsiva)
            for dia, eventos in eventos_por_dia.items():
                horas_dia = motor.calcular_horas_alocadas_no_dia(usuario_ativo, dia)
                pct_carga = min(1.0, horas_dia / max(1, usuario_ativo.limiteHorasDiarias))
                nome_dia = DIAS_SEMANA_PT[dia.weekday()]

                header_col, barra_col = st.columns([2.2, 1.3])
                header_col.markdown(f"### 📆 {nome_dia} — {dia.strftime('%d/%m/%Y')}")
                barra_col.progress(
                    pct_carga,
                    text=f"Carga de estudo (RN05): {horas_dia:.1f}h / {usuario_ativo.limiteHorasDiarias}h",
                )

                for ev in eventos:
                    ini_str = ev["inicio"].strftime("%H:%M")
                    fim_str = ev["fim"].strftime("%H:%M")

                    if ev["tipo"] == "FIXO":
                        comp: CompromissoFixo = ev["objeto"]
                        rec_txt = " • Recorrente semanal" if comp.recorrente else ""
                        st.markdown(
                            f"""
                            <div class="card-evento card-fixo">
                                <span class="badge badge-fixo">🔒 COMPROMISSO FIXO (RN01)</span>
                                <div class="evento-titulo">{comp.titulo}</div>
                                <div class="evento-meta">🕒 {ini_str} às {fim_str}{rec_txt}</div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                    else:
                        sessao: SessaoEstudo = ev["objeto"]
                        disc: Optional[Disciplina] = ev["disciplina"]
                        st_val = sessao.status.value
                        classe_css = f"card-{st_val.lower()}"
                        badge_css = f"badge-{st_val.lower()}"
                        frag_badge = (
                            "<span class='badge badge-frag'>🤖 Reagendado pela IA</span>"
                            if sessao.sessao_origem_id
                            else ""
                        )
                        deadline_txt = (
                            f" • Deadline: {disc.dataLimite.strftime('%d/%m/%Y')} • Dificuldade: {disc.grauDificuldade}/5"
                            if disc
                            else ""
                        )

                        col_card, col_acoes = st.columns([3.2, 1.5])
                        with col_card:
                            st.markdown(
                                f"""
                                <div class="card-evento {classe_css}">
                                    <span class="badge {badge_css}">{st_val}</span>{frag_badge}
                                    <div class="evento-titulo">📚 {ev['titulo']}</div>
                                    <div class="evento-meta">
                                        🕒 {ini_str} às {fim_str} ({sessao.duracao_horas:.1f}h){deadline_txt}
                                    </div>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )

                        with col_acoes:
                            if sessao.status == StatusSessao.AGENDADO:
                                acao_pendente = st.session_state.get("confirmar_acao")

                                # Fluxo de confirmação para UC06 (Concluir Sessão)
                                if acao_pendente == ("CONCLUIR", str(sessao.id)):
                                    st.warning("Confirmar conclusão (UC06)?")
                                    c_sim, c_nao = st.columns(2)
                                    if c_sim.button("Sim", key=f"conf_conc_{sessao.id}", type="primary"):
                                        usuario_ativo.concluirSessao(sessao)
                                        db.atualizar_status_sessao(sessao.id, StatusSessao.CONCLUIDO)
                                        st.session_state["confirmar_acao"] = None
                                        st.toast("Sessão marcada como CONCLUÍDO!", icon="✅")
                                        st.rerun()
                                    if c_nao.button("Cancelar", key=f"canc_conc_{sessao.id}"):
                                        st.session_state["confirmar_acao"] = None
                                        st.rerun()

                                # Fluxo de confirmação para UC07 (Informar Imprevisto -> UC08..UC12)
                                elif acao_pendente == ("IMPREVISTO", str(sessao.id)):
                                    st.error("Confirmar imprevisto e acionar IA (UC07)?")
                                    c_sim, c_nao = st.columns(2)
                                    if c_sim.button("Confirmar", key=f"conf_imp_{sessao.id}", type="primary"):
                                        usuario_ativo.reportarImprevisto(sessao)
                                        relatorio = motor.acionarReagendamento(usuario_ativo, sessao)
                                        persistir_relatorio_reagendamento(usuario_ativo, sessao, relatorio)
                                        st.session_state["ultimo_relatorio_ia"] = relatorio
                                        st.session_state["confirmar_acao"] = None
                                        st.toast("Motor de IA reorganizou sua agenda!", icon="🤖")
                                        st.rerun()
                                    if c_nao.button("Desistir", key=f"canc_imp_{sessao.id}"):
                                        st.session_state["confirmar_acao"] = None
                                        st.rerun()

                                else:
                                    if st.button(
                                        "✅ Concluir Sessão",
                                        key=f"btn_conc_{sessao.id}",
                                        use_container_width=True,
                                    ):
                                        st.session_state["confirmar_acao"] = ("CONCLUIR", str(sessao.id))
                                        st.rerun()

                                    if st.button(
                                        "⚠️ Tive um Imprevisto",
                                        key=f"btn_imp_{sessao.id}",
                                        use_container_width=True,
                                        type="primary",
                                    ):
                                        st.session_state["confirmar_acao"] = ("IMPREVISTO", str(sessao.id))
                                        st.rerun()
                            elif sessao.status == StatusSessao.ATRASADO:
                                st.caption("Sessão marcada como atrasada e processada pela IA.")
                            elif sessao.status == StatusSessao.CONCLUIDO:
                                st.caption("Estudo concluído com sucesso.")

                st.divider()

            # Visão Tabular Consolidada (Pandas)
            with st.expander("📊 Visualizar Tabela Cronológica Completa"):
                linhas = []
                for dia, eventos in eventos_por_dia.items():
                    for ev in eventos:
                        status_txt = (
                            "FIXO" if ev["tipo"] == "FIXO" else ev["objeto"].status.value
                        )
                        dur_h = round((ev["fim"] - ev["inicio"]).total_seconds() / 3600.0, 2)
                        linhas.append(
                            {
                                "Data": dia.strftime("%d/%m/%Y"),
                                "Dia": DIAS_SEMANA_PT[dia.weekday()],
                                "Início": ev["inicio"].strftime("%H:%M"),
                                "Fim": ev["fim"].strftime("%H:%M"),
                                "Duração (h)": dur_h,
                                "Tipo": "Compromisso Fixo" if ev["tipo"] == "FIXO" else "Sessão de Estudo",
                                "Atividade": ev["titulo"],
                                "Status": status_txt,
                            }
                        )
                st.dataframe(pd.DataFrame(linhas), use_container_width=True, hide_index=True)


# =============================================================================
# ABA 2: CADASTRAR SESSÃO DE ESTUDO (UC05 — RN01 & RN05)
# =============================================================================
with tab_agendar:
    st.subheader("UC05 — Agendamento Manual de Sessão de Estudo")
    if not usuario_ativo.disciplinas:
        st.warning("Cadastre pelo menos uma Disciplina/Meta de Estudo na aba **Disciplinas** (UC03) primeiro.")
    else:
        col_form, col_janelas = st.columns([1.3, 1.2])

        with col_form:
            disciplinas_priorizadas = motor.priorizarDisciplinas(usuario_ativo.disciplinas)
            opcoes_disc = {str(d.id): f"{d.nome} (Deadline: {d.dataLimite.strftime('%d/%m/%Y')})" for d in disciplinas_priorizadas}

            with st.form("form_nova_sessao"):
                disc_id_sel = st.selectbox(
                    "Disciplina / Meta de Estudo *",
                    options=list(opcoes_disc.keys()),
                    format_func=lambda did: opcoes_disc[did],
                )
                c1, c2, c3 = st.columns(3)
                data_sessao = c1.date_input("Data *", value=date.today())
                hora_inicio = c2.time_input("Horário de início *", value=time(14, 0), step=1800)
                duracao_horas = c3.selectbox(
                    "Duração *",
                    options=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
                    index=3,
                    format_func=lambda h: f"{h:.1f}h ({int(h * 60)} min)",
                )

                submitted_sessao = st.form_submit_button("Agendar Sessão", type="primary")
                if submitted_sessao:
                    dt_inicio = datetime.combine(data_sessao, hora_inicio)
                    dt_fim = dt_inicio + timedelta(minutes=int(duracao_horas * 60))

                    # Validação RN01 — Bloqueio imediato de conflitos
                    tem_conflito, motivo = motor.verificarConflitoAgenda(usuario_ativo, dt_inicio, dt_fim)
                    if tem_conflito:
                        st.error(f"🚫 **Bloqueio de Conflito (RN01):** {motivo} Escolha outro horário.")
                    # Validação RN05 — Limite de carga diária
                    elif not motor.validarLimiteDiario(usuario_ativo, data_sessao, duracao_horas):
                        horas_atuais = motor.calcular_horas_alocadas_no_dia(usuario_ativo, data_sessao)
                        st.error(
                            f"⚠️ **Limite de Carga Diária excedido (RN05):** Você já possui "
                            f"{horas_atuais:.1f}h neste dia e seu limite é {usuario_ativo.limiteHorasDiarias}h/dia. "
                            f"Escolha outro dia ou reduza a duração para prevenir burnout."
                        )
                    else:
                        nova_sessao = SessaoEstudo(
                            usuario_id=usuario_ativo.id,
                            disciplina_id=UUID(disc_id_sel),
                            dataHoraInicio=dt_inicio,
                            dataHoraFim=dt_fim,
                            status=StatusSessao.AGENDADO,
                        )
                        db.salvar_sessao(nova_sessao)
                        st.success(
                            f"✅ Sessão agendada para {dt_inicio.strftime('%d/%m/%Y %H:%M')} - "
                            f"{dt_fim.strftime('%H:%M')} com status **AGENDADO**!"
                        )
                        st.rerun()

        with col_janelas:
            st.markdown("#### 🤖 Janelas Livres Sugeridas pelo MotorIA (UC10)")
            disc_ref_id = st.selectbox(
                "Consultar janelas até o deadline de:",
                options=list(opcoes_disc.keys()),
                format_func=lambda did: opcoes_disc[did],
                key="consulta_janelas_disc",
            )
            disc_ref = mapa_disciplinas[UUID(disc_ref_id)]
            janelas = motor.buscarJanelasLivres(usuario_ativo, disc_ref.dataLimite)
            if janelas:
                df_j = pd.DataFrame(
                    [
                        {
                            "Data": f"{DIAS_SEMANA_PT[j['data'].weekday()][:3]} {j['data'].strftime('%d/%m')}",
                            "Turno": j["turno"],
                            "Horário": f"{j['inicio'].strftime('%H:%M')} - {j['fim'].strftime('%H:%M')}",
                            "Livre": f"{j['duracao_horas']:.1f}h",
                            "Cota Dia (RN05)": f"{j['capacidade_restante_dia_horas']:.1f}h",
                        }
                        for j in janelas[:15]
                    ]
                )
                st.dataframe(df_j, use_container_width=True, hide_index=True)
            else:
                st.info("Nenhuma janela livre disponível antes do deadline respeitando RN01 e RN05.")


# =============================================================================
# ABA 3: DISCIPLINAS / METAS DE ESTUDO (UC03 e UC11)
# =============================================================================
with tab_disciplinas:
    st.subheader("UC03 — Cadastro e Acompanhamento de Disciplinas / Metas")
    col_cad_d, col_list_d = st.columns([1.1, 1.7])

    with col_cad_d:
        with st.form("form_disciplina"):
            st.markdown("#### Nova Disciplina / Meta")
            nome_disc = st.text_input("Nome da disciplina *", placeholder="Ex.: Banco de Dados")
            carga_disc = st.number_input("Carga horária total (horas) *", min_value=1, max_value=200, value=4)
            deadline_disc = st.date_input("Data limite (Deadline) *", value=date.today() + timedelta(days=5))
            dificuldade_disc = st.slider("Grau de dificuldade (1 = Fácil, 5 = Muito Difícil) *", 1, 5, 4)

            if st.form_submit_button("Cadastrar Disciplina", type="primary"):
                try:
                    nova_disc = Disciplina(
                        usuario_id=usuario_ativo.id,
                        nome=nome_disc,
                        cargaHorariaTotal=int(carga_disc),
                        dataLimite=deadline_disc,
                        grauDificuldade=int(dificuldade_disc),
                    )
                    db.salvar_disciplina(nova_disc)
                    st.success(f"Disciplina '{nova_disc.nome}' cadastrada com sucesso!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erro ao cadastrar disciplina: {exc}")

    with col_list_d:
        st.markdown("#### Metas Ordenadas por Prioridade do MotorIA (RN03 — UC11)")
        disciplinas_ord = motor.priorizarDisciplinas(usuario_ativo.disciplinas)
        if not disciplinas_ord:
            st.info("Nenhuma disciplina cadastrada ainda.")
        else:
            for pos, disc in enumerate(disciplinas_ord, start=1):
                concluidas_h = disc.calcular_horas_concluidas()
                pendentes_h = disc.calcular_horas_pendentes_exatas()
                pct = min(1.0, concluidas_h / max(1, disc.cargaHorariaTotal))

                with st.container(border=True):
                    top_l, top_r = st.columns([3.5, 1])
                    top_l.markdown(
                        f"**#{pos} — {disc.nome}**  \n"
                        f"📅 Deadline: `{disc.dataLimite.strftime('%d/%m/%Y')}` • "
                        f"🔥 Dificuldade: `{disc.grauDificuldade}/5` • "
                        f"⏱️ Meta: `{disc.cargaHorariaTotal}h`"
                    )
                    if top_r.button("🗑️ Excluir", key=f"del_disc_{disc.id}"):
                        db.excluir_disciplina(disc.id)
                        st.rerun()

                    st.progress(
                        pct,
                        text=f"Progresso: {concluidas_h:.1f}h concluídas • {pendentes_h:.1f}h pendentes ({pct * 100:.0f}%)",
                    )


# =============================================================================
# ABA 4: COMPROMISSOS FIXOS (UC02 — RN01)
# =============================================================================
with tab_compromissos:
    st.subheader("UC02 — Cadastro de Compromissos Fixos (Rotina / Zonas Proibidas)")
    col_cad_c, col_list_c = st.columns([1.1, 1.7])

    with col_cad_c:
        with st.form("form_compromisso"):
            st.markdown("#### Novo Compromisso Fixo")
            titulo_comp = st.text_input("Título do compromisso *", placeholder="Ex.: Trabalho ou Aula 4ADS")
            data_comp = st.date_input("Data *", value=date.today())
            c_h1, c_h2 = st.columns(2)
            inicio_comp = c_h1.time_input("Início *", value=time(8, 0), step=1800)
            fim_comp = c_h2.time_input("Término *", value=time(12, 0), step=1800)
            recorrente_comp = st.checkbox("Compromisso recorrente semanalmente", value=False)

            if st.form_submit_button("Cadastrar Compromisso Fixo", type="primary"):
                try:
                    dt_ini = datetime.combine(data_comp, inicio_comp)
                    dt_fim = datetime.combine(data_comp, fim_comp)
                    novo_comp = CompromissoFixo(
                        usuario_id=usuario_ativo.id,
                        titulo=titulo_comp,
                        dataHoraInicio=dt_ini,
                        dataHoraFim=dt_fim,
                        recorrente=recorrente_comp,
                    )
                    # Valida conflito com outros compromissos fixos (Fluxo Alternativo UC02 / RN01)
                    usuario_ativo.adicionarCompromisso(novo_comp)
                    db.salvar_compromisso(novo_comp)
                    st.success(f"Compromisso '{novo_comp.titulo}' registrado na agenda!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erro ao cadastrar compromisso: {exc}")

    with col_list_c:
        st.markdown("#### Compromissos Fixos Cadastrados")
        if not usuario_ativo.compromissos:
            st.info("Nenhum compromisso fixo cadastrado.")
        else:
            for comp in usuario_ativo.compromissos:
                with st.container(border=True):
                    cl, cr = st.columns([4, 1])
                    dia_txt = DIAS_SEMANA_PT[comp.dataHoraInicio.weekday()]
                    rec_badge = " 🔁 Recorrente" if comp.recorrente else ""
                    cl.markdown(
                        f"**🔒 {comp.titulo}**{rec_badge}  \n"
                        f"📆 {dia_txt}, {comp.dataHoraInicio.strftime('%d/%m/%Y')} • "
                        f"🕒 {comp.dataHoraInicio.strftime('%H:%M')} às {comp.dataHoraFim.strftime('%H:%M')}"
                    )
                    if cr.button("🗑️ Excluir", key=f"del_comp_{comp.id}"):
                        db.excluir_compromisso(comp.id)
                        st.rerun()


# =============================================================================
# ABA 5: PERFIL DO ESTUDANTE (UC01 — RN05)
# =============================================================================
with tab_perfil:
    st.subheader("UC01 — Cadastro e Edição do Perfil do Estudante")
    col_edit, col_novo = st.columns(2)

    with col_edit:
        with st.form("form_editar_perfil"):
            st.markdown("#### Editar Perfil Ativo")
            nome_ed = st.text_input("Nome *", value=usuario_ativo.nome)
            email_ed = st.text_input("E-mail *", value=usuario_ativo.email)
            limite_ed = st.slider(
                "Limite máximo de horas de estudo diárias (RN05) *",
                min_value=1,
                max_value=12,
                value=min(12, usuario_ativo.limiteHorasDiarias),
            )
            turnos_ed = st.multiselect(
                "Turnos de maior produtividade *",
                options=["MANHA", "TARDE", "NOITE"],
                default=usuario_ativo.turnosProdutivos,
            )

            if st.form_submit_button("Salvar Alterações", type="primary"):
                try:
                    atualizado = Usuario(
                        id=usuario_ativo.id,
                        nome=nome_ed,
                        email=email_ed,
                        limiteHorasDiarias=int(limite_ed),
                        turnosProdutivos=turnos_ed,
                    )
                    db.salvar_usuario(atualizado)
                    st.success("Perfil atualizado com sucesso!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erro ao atualizar perfil: {exc}")

    with col_novo:
        with st.form("form_criar_novo_perfil"):
            st.markdown("#### Criar Novo Perfil de Estudante")
            nome_nv = st.text_input("Nome *", placeholder="Ex.: Yuri da Silva Araújo")
            email_nv = st.text_input("E-mail *", placeholder="Ex.: yuri.araujo@4ads.edu.br")
            limite_nv = st.slider("Limite máximo de horas/dia (RN05)", 1, 12, 4, key="lim_nv")
            turnos_nv = st.multiselect(
                "Turnos produtivos",
                options=["MANHA", "TARDE", "NOITE"],
                default=["MANHA", "NOITE"],
                key="turn_nv",
            )

            if st.form_submit_button("Criar Novo Perfil"):
                try:
                    novo_u = Usuario(
                        nome=nome_nv,
                        email=email_nv,
                        limiteHorasDiarias=int(limite_nv),
                        turnosProdutivos=turnos_nv,
                    )
                    db.salvar_usuario(novo_u)
                    st.session_state["usuario_ativo_id"] = str(novo_u.id)
                    st.success(f"Perfil '{novo_u.nome}' criado e ativado!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erro ao criar perfil: {exc}")


# =============================================================================
# ABA 6: NOTIFICAÇÕES DE REAGENDAMENTO (UC12)
# =============================================================================
with tab_notificacoes:
    st.subheader("UC12 — Histórico de Notificações do Motor de IA")
    notificacoes = db.listar_notificacoes(usuario_ativo.id)
    if not notificacoes:
        st.info(
            "Nenhuma notificação registrada ainda. Acione **⚠️ Tive um Imprevisto** em uma sessão "
            "agendada para visualizar o relatório detalhado do Motor de IA."
        )
    else:
        for notif in notificacoes:
            icone = "📩" if not notif["lida"] else "✅"
            with st.container(border=True):
                c_txt, c_btn = st.columns([4.2, 1])
                c_txt.markdown(f"**{icone} {notif['titulo']}**  \n*{notif['criado_em']}*")
                c_txt.write(notif["mensagem"])

                detalhes = notif.get("detalhes") or {}
                if detalhes.get("blocos"):
                    blocos_fmt = " | ".join(
                        f"{b['dia_semana']} ({b['data']}) {b['inicio']}-{b['fim']} ({b['duracao_horas']:.1f}h)"
                        for b in detalhes["blocos"]
                    )
                    c_txt.caption(f"Blocos alocados: {blocos_fmt}")

                if not notif["lida"]:
                    if c_btn.button("Marcar lida", key=f"lida_{notif['id']}"):
                        db.marcar_notificacao_lida(notif["id"])
                        st.rerun()
