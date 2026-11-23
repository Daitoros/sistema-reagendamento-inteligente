"""
Camada de Modelo de Domínio (Entidades e Enumerações) — Sistema de Reagendamento Inteligente.

Implementação orientada a objetos fiel ao Diagrama de Classes do MRDS:
- Enum StatusSessao
- Classe Usuario
- Classe CompromissoFixo
- Classe Disciplina
- Classe SessaoEstudo
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum
from typing import List, Optional
from uuid import UUID, uuid4


class StatusSessao(str, Enum):
    """Estados possíveis de uma Sessão de Estudo na agenda."""

    AGENDADO = "AGENDADO"
    CONCLUIDO = "CONCLUIDO"
    ATRASADO = "ATRASADO"
    CANCELADO = "CANCELADO"


@dataclass
class CompromissoFixo:
    """
    Representa um evento imutável na rotina do estudante (aulas, trabalho, compromissos pessoais).
    Atua como 'zona proibida' para agendamento/reagendamento de sessões de estudo (RN01).
    """

    usuario_id: UUID
    titulo: str
    dataHoraInicio: datetime
    dataHoraFim: datetime
    recorrente: bool = False
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            self.id = UUID(str(self.id))
        if not isinstance(self.usuario_id, UUID):
            self.usuario_id = UUID(str(self.usuario_id))
        if not self.titulo or not self.titulo.strip():
            raise ValueError("O título do compromisso fixo não pode ser vazio.")
        if self.dataHoraFim <= self.dataHoraInicio:
            raise ValueError("A data/hora de término deve ser posterior à data/hora de início.")
        self.titulo = self.titulo.strip()

    def verificarConflito(self, inicio: datetime, fim: datetime) -> bool:
        """
        Verifica se o intervalo [inicio, fim) sobrepõe este compromisso fixo (RN01).

        Quando `recorrente=True`, considera também a repetição semanal do compromisso
        no mesmo dia da semana e faixa horária a partir da data original.
        """
        if fim <= inicio:
            raise ValueError("O intervalo consultado deve ter fim posterior ao início.")

        # Sobreposição direta na data cadastrada
        if inicio < self.dataHoraFim and fim > self.dataHoraInicio:
            return True

        # Sobreposição por recorrência semanal (mesmo dia da semana a partir da data de início)
        if self.recorrente and inicio.date() >= self.dataHoraInicio.date():
            if inicio.weekday() == self.dataHoraInicio.weekday():
                ocorrencia_inicio = datetime.combine(inicio.date(), self.dataHoraInicio.time())
                ocorrencia_fim = datetime.combine(inicio.date(), self.dataHoraFim.time())
                if inicio < ocorrencia_fim and fim > ocorrencia_inicio:
                    return True

        return False

    def obter_ocorrencia_no_dia(self, dia: date) -> Optional[tuple[datetime, datetime]]:
        """
        Retorna a tupla (inicio, fim) deste compromisso no dia especificado, caso ele ocorra nesse dia.
        Útil para o MotorIA recortar zonas proibidas dia a dia.
        """
        inicio_dia = datetime.combine(dia, time.min)
        fim_dia = datetime.combine(dia, time.max)

        if self.dataHoraInicio <= fim_dia and self.dataHoraFim >= inicio_dia:
            return (max(self.dataHoraInicio, inicio_dia), min(self.dataHoraFim, fim_dia))

        if (
            self.recorrente
            and dia >= self.dataHoraInicio.date()
            and dia.weekday() == self.dataHoraInicio.weekday()
        ):
            return (
                datetime.combine(dia, self.dataHoraInicio.time()),
                datetime.combine(dia, self.dataHoraFim.time()),
            )

        return None


@dataclass
class SessaoEstudo:
    """
    Representa um bloco de tempo específico alocado na agenda para estudo de uma Disciplina.
    """

    usuario_id: UUID
    disciplina_id: UUID
    dataHoraInicio: datetime
    dataHoraFim: datetime
    status: StatusSessao = StatusSessao.AGENDADO
    id: UUID = field(default_factory=uuid4)
    sessao_origem_id: Optional[UUID] = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            self.id = UUID(str(self.id))
        if not isinstance(self.usuario_id, UUID):
            self.usuario_id = UUID(str(self.usuario_id))
        if not isinstance(self.disciplina_id, UUID):
            self.disciplina_id = UUID(str(self.disciplina_id))
        if self.sessao_origem_id is not None and not isinstance(self.sessao_origem_id, UUID):
            self.sessao_origem_id = UUID(str(self.sessao_origem_id))
        if not isinstance(self.status, StatusSessao):
            self.status = StatusSessao(str(self.status).upper())
        if self.dataHoraFim <= self.dataHoraInicio:
            raise ValueError("A data/hora de término da sessão deve ser posterior ao início.")

    def alterarStatus(self, novoStatus: StatusSessao) -> None:
        """Atualiza o status da sessão de estudo de forma validada."""
        if not isinstance(novoStatus, StatusSessao):
            novoStatus = StatusSessao(str(novoStatus).upper())
        self.status = novoStatus

    def obterDuracao(self) -> int:
        """Retorna a duração total da sessão em minutos (inteiro)."""
        return int((self.dataHoraFim - self.dataHoraInicio).total_seconds() // 60)

    @property
    def duracao_horas(self) -> float:
        """Retorna a duração da sessão em horas (ponto flutuante)."""
        return self.obterDuracao() / 60.0

    def verificarConflito(self, inicio: datetime, fim: datetime) -> bool:
        """
        Verifica sobreposição temporal com outra atividade.
        Apenas sessões ativas (AGENDADO ou CONCLUIDO) bloqueiam horário na agenda.
        """
        if self.status not in (StatusSessao.AGENDADO, StatusSessao.CONCLUIDO):
            return False
        return inicio < self.dataHoraFim and fim > self.dataHoraInicio


@dataclass
class Disciplina:
    """
    Representa uma matéria ou meta de estudo com carga horária total, deadline e grau de dificuldade.
    """

    usuario_id: UUID
    nome: str
    cargaHorariaTotal: int
    dataLimite: date
    grauDificuldade: int = 3
    id: UUID = field(default_factory=uuid4)
    sessoes: List[SessaoEstudo] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            self.id = UUID(str(self.id))
        if not isinstance(self.usuario_id, UUID):
            self.usuario_id = UUID(str(self.usuario_id))
        if not self.nome or not self.nome.strip():
            raise ValueError("O nome da disciplina não pode ser vazio.")
        if self.cargaHorariaTotal <= 0:
            raise ValueError("A carga horária total deve ser maior que zero.")
        if not (1 <= self.grauDificuldade <= 5):
            raise ValueError("O grau de dificuldade deve estar entre 1 e 5.")
        self.nome = self.nome.strip()

    def calcular_horas_concluidas(self, sessoes: Optional[List[SessaoEstudo]] = None) -> float:
        """Calcula o total exato (em horas) de sessões concluídas desta disciplina."""
        lista = sessoes if sessoes is not None else self.sessoes
        minutos_concluidos = sum(
            s.obterDuracao()
            for s in lista
            if s.disciplina_id == self.id and s.status == StatusSessao.CONCLUIDO
        )
        return round(minutos_concluidos / 60.0, 2)

    def calcular_horas_pendentes_exatas(self, sessoes: Optional[List[SessaoEstudo]] = None) -> float:
        """Retorna as horas pendentes com precisão decimal (para barras de progresso)."""
        restante = self.cargaHorariaTotal - self.calcular_horas_concluidas(sessoes)
        return round(max(0.0, restante), 2)

    def calcularHorasPendentes(self, sessoes: Optional[List[SessaoEstudo]] = None) -> int:
        """
        Retorna a quantidade inteira de horas ainda pendentes para concluir a meta da disciplina.
        """
        restante = self.calcular_horas_pendentes_exatas(sessoes)
        return int(math.ceil(restante)) if restante > 0 else 0


@dataclass
class Usuario:
    """
    Representa o perfil do estudante e agrega seus compromissos fixos, disciplinas e sessões.
    """

    nome: str
    email: str
    limiteHorasDiarias: int = 4
    turnosProdutivos: List[str] = field(default_factory=lambda: ["MANHA", "TARDE", "NOITE"])
    id: UUID = field(default_factory=uuid4)
    compromissos: List[CompromissoFixo] = field(default_factory=list, repr=False)
    disciplinas: List[Disciplina] = field(default_factory=list, repr=False)
    sessoes: List[SessaoEstudo] = field(default_factory=list, repr=False)

    TURNOS_VALIDOS = ("MANHA", "TARDE", "NOITE")

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            self.id = UUID(str(self.id))
        if not self.nome or not self.nome.strip():
            raise ValueError("O nome do estudante não pode ser vazio.")
        if not self.email or "@" not in self.email:
            raise ValueError("Informe um endereço de e-mail válido.")
        if not (1 <= self.limiteHorasDiarias <= 24):
            raise ValueError("O limite de horas diárias deve estar entre 1 e 24.")
        self.nome = self.nome.strip()
        self.email = self.email.strip()

        turnos_normalizados: List[str] = []
        for turno in self.turnosProdutivos:
            t = str(turno).strip().upper()
            if t not in self.TURNOS_VALIDOS:
                raise ValueError(
                    f"Turno produtivo inválido: '{turno}'. Use um de {self.TURNOS_VALIDOS}."
                )
            if t not in turnos_normalizados:
                turnos_normalizados.append(t)
        self.turnosProdutivos = turnos_normalizados or ["MANHA", "TARDE", "NOITE"]

    def adicionarCompromisso(self, compromisso: CompromissoFixo) -> None:
        """
        Adiciona um compromisso fixo à agenda do estudante (UC02), validando conflitos
        com compromissos fixos já cadastrados (RN01).
        """
        for existente in self.compromissos:
            if existente.id == compromisso.id:
                continue
            if existente.verificarConflito(compromisso.dataHoraInicio, compromisso.dataHoraFim):
                raise ValueError(
                    f"Conflito de horário detectado (RN01): '{compromisso.titulo}' "
                    f"sobrepõe o compromisso fixo '{existente.titulo}' "
                    f"({existente.dataHoraInicio.strftime('%d/%m %H:%M')} - "
                    f"{existente.dataHoraFim.strftime('%H:%M')})."
                )
        compromisso.usuario_id = self.id
        self.compromissos.append(compromisso)

    def reportarImprevisto(self, sessao: SessaoEstudo) -> None:
        """
        Marca uma sessão de estudo agendada como ATRASADO devido a um imprevisto (UC07 / RN02).
        """
        if sessao.status == StatusSessao.CONCLUIDO:
            raise ValueError("Não é possível reportar imprevisto em uma sessão já concluída.")
        if sessao.status == StatusSessao.CANCELADO:
            raise ValueError("Não é possível reportar imprevisto em uma sessão cancelada.")
        sessao.alterarStatus(StatusSessao.ATRASADO)

    def concluirSessao(self, sessao: SessaoEstudo) -> None:
        """
        Marca uma sessão de estudo como CONCLUIDO (UC06).
        """
        if sessao.status == StatusSessao.CANCELADO:
            raise ValueError("Não é possível concluir uma sessão cancelada.")
        sessao.alterarStatus(StatusSessao.CONCLUIDO)

    def obter_disciplina(self, disciplina_id: UUID) -> Optional[Disciplina]:
        """Localiza uma disciplina do usuário pelo seu UUID."""
        for disciplina in self.disciplinas:
            if disciplina.id == disciplina_id:
                return disciplina
        return None
