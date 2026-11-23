"""
Camada de Persistência SQLite — Sistema de Reagendamento Inteligente.

Responsável por inicializar o schema relacional, executar operações CRUD para
todas as entidades (Usuario, CompromissoFixo, Disciplina, SessaoEstudo e Notificacoes)
e popular o banco com o Cenário de Exemplo fiel ao Fluxo Principal do MRDS.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional
from uuid import UUID, uuid4

from models import CompromissoFixo, Disciplina, SessaoEstudo, StatusSessao, Usuario


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "reagendamento_inteligente.db"

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL CHECK (length(trim(nome)) > 0),
    email TEXT NOT NULL UNIQUE CHECK (instr(email, '@') > 1),
    limite_horas_diarias INTEGER NOT NULL DEFAULT 4
        CHECK (limite_horas_diarias >= 1 AND limite_horas_diarias <= 24),
    turnos_produtivos TEXT NOT NULL DEFAULT '["MANHA","TARDE","NOITE"]',
    criado_em TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    atualizado_em TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS compromissos_fixos (
    id TEXT PRIMARY KEY,
    usuario_id TEXT NOT NULL,
    titulo TEXT NOT NULL CHECK (length(trim(titulo)) > 0),
    data_hora_inicio TEXT NOT NULL,
    data_hora_fim TEXT NOT NULL,
    recorrente INTEGER NOT NULL DEFAULT 0 CHECK (recorrente IN (0, 1)),
    CHECK (data_hora_fim > data_hora_inicio),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS disciplinas (
    id TEXT PRIMARY KEY,
    usuario_id TEXT NOT NULL,
    nome TEXT NOT NULL CHECK (length(trim(nome)) > 0),
    carga_horaria_total INTEGER NOT NULL CHECK (carga_horaria_total > 0),
    data_limite TEXT NOT NULL,
    grau_dificuldade INTEGER NOT NULL DEFAULT 3
        CHECK (grau_dificuldade >= 1 AND grau_dificuldade <= 5),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sessoes_estudo (
    id TEXT PRIMARY KEY,
    usuario_id TEXT NOT NULL,
    disciplina_id TEXT NOT NULL,
    data_hora_inicio TEXT NOT NULL,
    data_hora_fim TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'AGENDADO'
        CHECK (status IN ('AGENDADO', 'CONCLUIDO', 'ATRASADO', 'CANCELADO')),
    sessao_origem_id TEXT NULL,
    CHECK (data_hora_fim > data_hora_inicio),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
    FOREIGN KEY (disciplina_id) REFERENCES disciplinas(id) ON DELETE CASCADE,
    FOREIGN KEY (sessao_origem_id) REFERENCES sessoes_estudo(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS notificacoes (
    id TEXT PRIMARY KEY,
    usuario_id TEXT NOT NULL,
    sessao_id TEXT NULL,
    titulo TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    detalhes_json TEXT NULL,
    lida INTEGER NOT NULL DEFAULT 0 CHECK (lida IN (0, 1)),
    criado_em TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
    FOREIGN KEY (sessao_id) REFERENCES sessoes_estudo(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_compromissos_usuario_periodo
    ON compromissos_fixos (usuario_id, data_hora_inicio, data_hora_fim);

CREATE INDEX IF NOT EXISTS idx_disciplinas_usuario_deadline
    ON disciplinas (usuario_id, data_limite, grau_dificuldade DESC);

CREATE INDEX IF NOT EXISTS idx_sessoes_usuario_periodo
    ON sessoes_estudo (usuario_id, data_hora_inicio, data_hora_fim);

CREATE INDEX IF NOT EXISTS idx_sessoes_usuario_status
    ON sessoes_estudo (usuario_id, status);
"""


class DatabaseManager:
    """Gerencia conexões e operações CRUD no banco relacional SQLite."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        self.inicializar_banco()

    @contextmanager
    def _conectar(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def inicializar_banco(self) -> None:
        """Cria todas as tabelas e índices caso ainda não existam."""
        with self._conectar() as conn:
            conn.executescript(SCHEMA_SQL)

    def limpar_banco(self) -> None:
        """Remove todos os registros (útil para resetar e recarregar cenário de exemplo)."""
        with self._conectar() as conn:
            conn.execute("DELETE FROM notificacoes;")
            conn.execute("DELETE FROM sessoes_estudo;")
            conn.execute("DELETE FROM disciplinas;")
            conn.execute("DELETE FROM compromissos_fixos;")
            conn.execute("DELETE FROM usuarios;")

    # -------------------------------------------------------------------------
    # CRUD — Usuario (UC01)
    # -------------------------------------------------------------------------

    def salvar_usuario(self, usuario: Usuario) -> Usuario:
        """Insere ou atualiza um perfil de estudante no banco."""
        with self._conectar() as conn:
            conn.execute(
                """
                INSERT INTO usuarios (id, nome, email, limite_horas_diarias, turnos_produtivos, atualizado_em)
                VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(id) DO UPDATE SET
                    nome = excluded.nome,
                    email = excluded.email,
                    limite_horas_diarias = excluded.limite_horas_diarias,
                    turnos_produtivos = excluded.turnos_produtivos,
                    atualizado_em = datetime('now', 'localtime')
                """,
                (
                    str(usuario.id),
                    usuario.nome,
                    usuario.email,
                    usuario.limiteHorasDiarias,
                    json.dumps(usuario.turnosProdutivos, ensure_ascii=False),
                ),
            )
        return usuario

    def listar_usuarios(self) -> List[Usuario]:
        """Retorna todos os perfis de estudantes cadastrados."""
        with self._conectar() as conn:
            rows = conn.execute("SELECT * FROM usuarios ORDER BY nome ASC;").fetchall()
        return [self._row_para_usuario(r) for r in rows]

    def obter_usuario(self, usuario_id: UUID | str) -> Optional[Usuario]:
        """Busca um usuário pelo UUID sem carregar coleções relacionadas."""
        with self._conectar() as conn:
            row = conn.execute(
                "SELECT * FROM usuarios WHERE id = ?;", (str(usuario_id),)
            ).fetchone()
        return self._row_para_usuario(row) if row else None

    def carregar_usuario_completo(self, usuario_id: UUID | str) -> Optional[Usuario]:
        """Carrega o Usuário junto com seus Compromissos, Disciplinas e Sessões."""
        usuario = self.obter_usuario(usuario_id)
        if usuario is None:
            return None

        usuario.compromissos = self.listar_compromissos(usuario.id)
        usuario.disciplinas = self.listar_disciplinas(usuario.id)
        usuario.sessoes = self.listar_sessoes(usuario.id)

        # Vincula as sessões em cada objeto Disciplina para calcularHorasPendentes()
        mapa_disciplinas: Dict[UUID, Disciplina] = {d.id: d for d in usuario.disciplinas}
        for sessao in usuario.sessoes:
            disc = mapa_disciplinas.get(sessao.disciplina_id)
            if disc is not None:
                disc.sessoes.append(sessao)

        return usuario

    def excluir_usuario(self, usuario_id: UUID | str) -> None:
        with self._conectar() as conn:
            conn.execute("DELETE FROM usuarios WHERE id = ?;", (str(usuario_id),))

    # -------------------------------------------------------------------------
    # CRUD — CompromissoFixo (UC02)
    # -------------------------------------------------------------------------

    def salvar_compromisso(self, compromisso: CompromissoFixo) -> CompromissoFixo:
        with self._conectar() as conn:
            conn.execute(
                """
                INSERT INTO compromissos_fixos
                    (id, usuario_id, titulo, data_hora_inicio, data_hora_fim, recorrente)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    titulo = excluded.titulo,
                    data_hora_inicio = excluded.data_hora_inicio,
                    data_hora_fim = excluded.data_hora_fim,
                    recorrente = excluded.recorrente
                """,
                (
                    str(compromisso.id),
                    str(compromisso.usuario_id),
                    compromisso.titulo,
                    compromisso.dataHoraInicio.isoformat(),
                    compromisso.dataHoraFim.isoformat(),
                    1 if compromisso.recorrente else 0,
                ),
            )
        return compromisso

    def listar_compromissos(self, usuario_id: UUID | str) -> List[CompromissoFixo]:
        with self._conectar() as conn:
            rows = conn.execute(
                """
                SELECT * FROM compromissos_fixos
                WHERE usuario_id = ?
                ORDER BY data_hora_inicio ASC;
                """,
                (str(usuario_id),),
            ).fetchall()
        return [
            CompromissoFixo(
                id=UUID(r["id"]),
                usuario_id=UUID(r["usuario_id"]),
                titulo=r["titulo"],
                dataHoraInicio=datetime.fromisoformat(r["data_hora_inicio"]),
                dataHoraFim=datetime.fromisoformat(r["data_hora_fim"]),
                recorrente=bool(r["recorrente"]),
            )
            for r in rows
        ]

    def excluir_compromisso(self, compromisso_id: UUID | str) -> None:
        with self._conectar() as conn:
            conn.execute("DELETE FROM compromissos_fixos WHERE id = ?;", (str(compromisso_id),))

    # -------------------------------------------------------------------------
    # CRUD — Disciplina (UC03)
    # -------------------------------------------------------------------------

    def salvar_disciplina(self, disciplina: Disciplina) -> Disciplina:
        with self._conectar() as conn:
            conn.execute(
                """
                INSERT INTO disciplinas
                    (id, usuario_id, nome, carga_horaria_total, data_limite, grau_dificuldade)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    nome = excluded.nome,
                    carga_horaria_total = excluded.carga_horaria_total,
                    data_limite = excluded.data_limite,
                    grau_dificuldade = excluded.grau_dificuldade
                """,
                (
                    str(disciplina.id),
                    str(disciplina.usuario_id),
                    disciplina.nome,
                    disciplina.cargaHorariaTotal,
                    disciplina.dataLimite.isoformat(),
                    disciplina.grauDificuldade,
                ),
            )
        return disciplina

    def listar_disciplinas(self, usuario_id: UUID | str) -> List[Disciplina]:
        with self._conectar() as conn:
            rows = conn.execute(
                """
                SELECT * FROM disciplinas
                WHERE usuario_id = ?
                ORDER BY data_limite ASC, grau_dificuldade DESC, nome ASC;
                """,
                (str(usuario_id),),
            ).fetchall()
        return [
            Disciplina(
                id=UUID(r["id"]),
                usuario_id=UUID(r["usuario_id"]),
                nome=r["nome"],
                cargaHorariaTotal=int(r["carga_horaria_total"]),
                dataLimite=date.fromisoformat(r["data_limite"]),
                grauDificuldade=int(r["grau_dificuldade"]),
            )
            for r in rows
        ]

    def excluir_disciplina(self, disciplina_id: UUID | str) -> None:
        with self._conectar() as conn:
            conn.execute("DELETE FROM disciplinas WHERE id = ?;", (str(disciplina_id),))

    # -------------------------------------------------------------------------
    # CRUD — SessaoEstudo (UC05, UC06, UC07, UC08, UC09)
    # -------------------------------------------------------------------------

    def salvar_sessao(self, sessao: SessaoEstudo) -> SessaoEstudo:
        with self._conectar() as conn:
            conn.execute(
                """
                INSERT INTO sessoes_estudo
                    (id, usuario_id, disciplina_id, data_hora_inicio, data_hora_fim, status, sessao_origem_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    disciplina_id = excluded.disciplina_id,
                    data_hora_inicio = excluded.data_hora_inicio,
                    data_hora_fim = excluded.data_hora_fim,
                    status = excluded.status,
                    sessao_origem_id = excluded.sessao_origem_id
                """,
                (
                    str(sessao.id),
                    str(sessao.usuario_id),
                    str(sessao.disciplina_id),
                    sessao.dataHoraInicio.isoformat(),
                    sessao.dataHoraFim.isoformat(),
                    sessao.status.value,
                    str(sessao.sessao_origem_id) if sessao.sessao_origem_id else None,
                ),
            )
        return sessao

    def atualizar_status_sessao(self, sessao_id: UUID | str, status: StatusSessao) -> None:
        with self._conectar() as conn:
            conn.execute(
                "UPDATE sessoes_estudo SET status = ? WHERE id = ?;",
                (status.value, str(sessao_id)),
            )

    def listar_sessoes(self, usuario_id: UUID | str) -> List[SessaoEstudo]:
        with self._conectar() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sessoes_estudo
                WHERE usuario_id = ?
                ORDER BY data_hora_inicio ASC;
                """,
                (str(usuario_id),),
            ).fetchall()
        return [
            SessaoEstudo(
                id=UUID(r["id"]),
                usuario_id=UUID(r["usuario_id"]),
                disciplina_id=UUID(r["disciplina_id"]),
                dataHoraInicio=datetime.fromisoformat(r["data_hora_inicio"]),
                dataHoraFim=datetime.fromisoformat(r["data_hora_fim"]),
                status=StatusSessao(r["status"]),
                sessao_origem_id=UUID(r["sessao_origem_id"]) if r["sessao_origem_id"] else None,
            )
            for r in rows
        ]

    def excluir_sessao(self, sessao_id: UUID | str) -> None:
        with self._conectar() as conn:
            conn.execute("DELETE FROM sessoes_estudo WHERE id = ?;", (str(sessao_id),))

    # -------------------------------------------------------------------------
    # CRUD — Notificacoes (UC12)
    # -------------------------------------------------------------------------

    def salvar_notificacao(
        self,
        usuario_id: UUID | str,
        titulo: str,
        mensagem: str,
        sessao_id: Optional[UUID | str] = None,
        detalhes: Optional[dict] = None,
    ) -> str:
        notif_id = str(uuid4())
        with self._conectar() as conn:
            conn.execute(
                """
                INSERT INTO notificacoes (id, usuario_id, sessao_id, titulo, mensagem, detalhes_json, lida)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    notif_id,
                    str(usuario_id),
                    str(sessao_id) if sessao_id else None,
                    titulo,
                    mensagem,
                    json.dumps(detalhes, ensure_ascii=False) if detalhes else None,
                ),
            )
        return notif_id

    def listar_notificacoes(self, usuario_id: UUID | str, limite: int = 20) -> List[dict]:
        with self._conectar() as conn:
            rows = conn.execute(
                """
                SELECT * FROM notificacoes
                WHERE usuario_id = ?
                ORDER BY criado_em DESC
                LIMIT ?;
                """,
                (str(usuario_id), limite),
            ).fetchall()

        resultado: List[dict] = []
        for r in rows:
            resultado.append(
                {
                    "id": r["id"],
                    "usuario_id": r["usuario_id"],
                    "sessao_id": r["sessao_id"],
                    "titulo": r["titulo"],
                    "mensagem": r["mensagem"],
                    "detalhes": json.loads(r["detalhes_json"]) if r["detalhes_json"] else {},
                    "lida": bool(r["lida"]),
                    "criado_em": r["criado_em"],
                }
            )
        return resultado

    def marcar_notificacao_lida(self, notif_id: str) -> None:
        with self._conectar() as conn:
            conn.execute("UPDATE notificacoes SET lida = 1 WHERE id = ?;", (notif_id,))

    # -------------------------------------------------------------------------
    # Utilitários Internos
    # -------------------------------------------------------------------------

    @staticmethod
    def _row_para_usuario(row: sqlite3.Row) -> Usuario:
        return Usuario(
            id=UUID(row["id"]),
            nome=row["nome"],
            email=row["email"],
            limiteHorasDiarias=int(row["limite_horas_diarias"]),
            turnosProdutivos=json.loads(row["turnos_produtivos"]),
        )


def calcular_segunda_semana_demonstracao(hoje: Optional[date] = None) -> date:
    """
    Retorna a segunda-feira ideal para o cenário de exemplo:
    - Se hoje for segunda ou terça antes do fim do dia, usa a semana atual;
    - Caso contrário, usa a próxima segunda-feira, garantindo que a sessão de terça-feira
      esteja sempre AGENDADA e pronta para testar 'Tive um imprevisto'.
    """
    ref = hoje or date.today()
    segunda_atual = ref - timedelta(days=ref.weekday())
    if ref.weekday() <= 0:
        return segunda_atual
    return segunda_atual + timedelta(days=7)


def popular_cenario_exemplo(db: DatabaseManager) -> Usuario:
    """
    Popula o banco de dados exatamente com o Fluxo Principal especificado no MRDS:
    - Estudante com limite de 3h/dia e turnos produtivos (MANHA, TARDE, NOITE);
    - Compromissos fixos da semana (Trabalho, Aulas 4ADS e Reunião na Quarta);
    - Disciplina 'Banco de Dados' (4h totais, deadline sexta-feira, dificuldade 4)
      com 2 blocos de 2h na terça (14h-16h) e quinta (14h-16h);
    - Disciplina secundária 'Engenharia de Software (MRDS)' (4h, deadline semana seguinte)
      para demonstrar RN03 (priorização por deadline).

    Ao acionar 'Tive um imprevisto' na sessão de Terça-feira (2h):
    - Quarta-feira tem apenas 1h livre (17h-18h) devido aos compromissos fixos (RN01);
    - Quinta-feira já tem 2h agendadas e o limite diário é 3h (RN05), sobrando 1h;
    - O Motor de IA fragmenta automaticamente em dois blocos de 1h (Quarta e Quinta) (RN04)!
    """
    db.limpar_banco()

    segunda = calcular_segunda_semana_demonstracao()
    terca = segunda + timedelta(days=1)
    quarta = segunda + timedelta(days=2)
    quinta = segunda + timedelta(days=3)
    sexta = segunda + timedelta(days=4)
    prox_terca = segunda + timedelta(days=8)

    # 1. Perfil do Estudante (UC01)
    estudante = Usuario(
        nome="Davi Aires Medeiros",
        email="davi.medeiros@4ads.edu.br",
        limiteHorasDiarias=3,
        turnosProdutivos=["MANHA", "TARDE", "NOITE"],
    )
    db.salvar_usuario(estudante)

    # 2. Compromissos Fixos da Semana (UC02 / RN01)
    compromissos: List[CompromissoFixo] = []
    for dia in (segunda, terca, quarta, quinta, sexta):
        compromissos.append(
            CompromissoFixo(
                usuario_id=estudante.id,
                titulo="Trabalho (Estágio TI)",
                dataHoraInicio=datetime.combine(dia, time(8, 0)),
                dataHoraFim=datetime.combine(dia, time(12, 0)),
                recorrente=False,
            )
        )
        # Na terça-feira, compromisso à tarde (16h-18h) e aulas + deslocamento (19h-23h)
        # garantem que, ao perder o bloco de 14h-16h, a IA precise buscar janelas em Quarta e Quinta.
        if dia == terca:
            compromissos.append(
                CompromissoFixo(
                    usuario_id=estudante.id,
                    titulo="Monitoria / Atividade Complementar",
                    dataHoraInicio=datetime.combine(dia, time(16, 0)),
                    dataHoraFim=datetime.combine(dia, time(18, 0)),
                    recorrente=False,
                )
            )
            compromissos.append(
                CompromissoFixo(
                    usuario_id=estudante.id,
                    titulo="Faculdade — Aulas 4ADS (Noite)",
                    dataHoraInicio=datetime.combine(dia, time(19, 0)),
                    dataHoraFim=datetime.combine(dia, time(23, 0)),
                    recorrente=False,
                )
            )
        # Na quarta-feira, reunião (13h-17h) e aula + laboratório (19h-23h) deixam
        # exatamente 1h livre (17h-18h) para demonstrar a fragmentação (RN04) de forma clara.
        elif dia == quarta:
            compromissos.append(
                CompromissoFixo(
                    usuario_id=estudante.id,
                    titulo="Reunião de Projeto Integrador",
                    dataHoraInicio=datetime.combine(dia, time(13, 0)),
                    dataHoraFim=datetime.combine(dia, time(17, 0)),
                    recorrente=False,
                )
            )
            compromissos.append(
                CompromissoFixo(
                    usuario_id=estudante.id,
                    titulo="Faculdade 4ADS + Laboratório",
                    dataHoraInicio=datetime.combine(dia, time(19, 0)),
                    dataHoraFim=datetime.combine(dia, time(23, 0)),
                    recorrente=False,
                )
            )
        elif dia == sexta:
            compromissos.append(
                CompromissoFixo(
                    usuario_id=estudante.id,
                    titulo="Entrega de Sprint no Estágio",
                    dataHoraInicio=datetime.combine(dia, time(13, 0)),
                    dataHoraFim=datetime.combine(dia, time(17, 0)),
                    recorrente=False,
                )
            )
            compromissos.append(
                CompromissoFixo(
                    usuario_id=estudante.id,
                    titulo="Faculdade — Aulas 4ADS",
                    dataHoraInicio=datetime.combine(dia, time(19, 0)),
                    dataHoraFim=datetime.combine(dia, time(22, 0)),
                    recorrente=False,
                )
            )
        else:
            compromissos.append(
                CompromissoFixo(
                    usuario_id=estudante.id,
                    titulo="Faculdade — Aulas 4ADS",
                    dataHoraInicio=datetime.combine(dia, time(19, 0)),
                    dataHoraFim=datetime.combine(dia, time(22, 0)),
                    recorrente=False,
                )
            )

    for comp in compromissos:
        db.salvar_compromisso(comp)

    # 3. Disciplinas / Metas de Estudo (UC03 / RN03)
    disc_bd = Disciplina(
        usuario_id=estudante.id,
        nome="Banco de Dados",
        cargaHorariaTotal=4,
        dataLimite=sexta,
        grauDificuldade=4,
    )
    disc_mrds = Disciplina(
        usuario_id=estudante.id,
        nome="Engenharia de Software (MRDS)",
        cargaHorariaTotal=4,
        dataLimite=prox_terca,
        grauDificuldade=3,
    )
    db.salvar_disciplina(disc_bd)
    db.salvar_disciplina(disc_mrds)

    # 4. Sessões de Estudo Iniciais — Dois blocos de 2h na terça e quinta (Fluxo Principal PDF)
    sessao_terca = SessaoEstudo(
        usuario_id=estudante.id,
        disciplina_id=disc_bd.id,
        dataHoraInicio=datetime.combine(terca, time(14, 0)),
        dataHoraFim=datetime.combine(terca, time(16, 0)),
        status=StatusSessao.AGENDADO,
    )
    sessao_quinta = SessaoEstudo(
        usuario_id=estudante.id,
        disciplina_id=disc_bd.id,
        dataHoraInicio=datetime.combine(quinta, time(14, 0)),
        dataHoraFim=datetime.combine(quinta, time(16, 0)),
        status=StatusSessao.AGENDADO,
    )
    db.salvar_sessao(sessao_terca)
    db.salvar_sessao(sessao_quinta)

    return db.carregar_usuario_completo(estudante.id) or estudante
