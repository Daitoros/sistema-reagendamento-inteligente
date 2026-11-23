"""
Serviço Inteligente (MotorIA) — Sistema de Reagendamento Inteligente.

Implementa as 5 Regras de Negócio (RN01 a RN05) e os Casos de Uso autônomos (UC08 a UC12):
- RN01: Bloqueio de Conflitos (Compromissos Fixos e outras Sessões de Estudo)
- RN02: Gatilho de Reagendamento (Manual "Tive um imprevisto" e Automático para sessões vencidas)
- RN03: Priorização por Prazo (Deadline mais próximo + grauDificuldade como desempate)
- RN04: Fragmentação de Blocos (divisão em blocos menores quando não há janela contínua)
- RN05: Limite de Carga Diária (prevenção de burnout respeitando limiteHorasDiarias)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from models import CompromissoFixo, Disciplina, SessaoEstudo, StatusSessao, Usuario


# Faixas horárias padrão para cada turno produtivo do estudante
FAIXAS_TURNOS: Dict[str, Tuple[time, time]] = {
    "MANHA": (time(8, 0), time(12, 0)),
    "TARDE": (time(13, 0), time(18, 0)),
    "NOITE": (time(19, 0), time(23, 0)),
}

# Menor unidade de fragmentação permitida (em minutos) para manter estudo produtivo
BLOCO_MINIMO_MINUTOS = 30


class MotorIA:
    """
    Cérebro do Sistema de Reagendamento Inteligente.
    Analisa a agenda do estudante, respeita compromissos fixos e limites diários,
    prioriza metas por deadline e remaneja/fragmenta sessões atrasadas.
    """

    def __init__(self, regrasPriorizacao: Optional[List[str]] = None) -> None:
        self.regrasPriorizacao: List[str] = regrasPriorizacao or [
            "DEADLINE_PROXIMO",
            "GRAU_DIFICULDADE",
            "TURNO_PRODUTIVO",
            "LIMITE_DIARIO",
        ]

    # -------------------------------------------------------------------------
    # Validações de Regras de Negócio (RN01 e RN05)
    # -------------------------------------------------------------------------

    def verificarConflitoAgenda(
        self,
        usuario: Usuario,
        inicio: datetime,
        fim: datetime,
        ignorar_sessao_id: Optional[UUID] = None,
    ) -> Tuple[bool, str]:
        """
        Valida a RN01 (Bloqueio de Conflitos): verifica se [inicio, fim) sobrepõe
        qualquer CompromissoFixo ou outra SessaoEstudo ativa (AGENDADO/CONCLUIDO).
        """
        for comp in usuario.compromissos:
            if comp.verificarConflito(inicio, fim):
                return (
                    True,
                    f"Conflito com compromisso fixo '{comp.titulo}' "
                    f"({comp.dataHoraInicio.strftime('%d/%m %H:%M')} - "
                    f"{comp.dataHoraFim.strftime('%H:%M')}).",
                )

        for sessao in usuario.sessoes:
            if ignorar_sessao_id is not None and sessao.id == ignorar_sessao_id:
                continue
            if sessao.verificarConflito(inicio, fim):
                disc = usuario.obter_disciplina(sessao.disciplina_id)
                nome_disc = disc.nome if disc else "Disciplina"
                return (
                    True,
                    f"Conflito com sessão de estudo de '{nome_disc}' "
                    f"({sessao.dataHoraInicio.strftime('%d/%m %H:%M')} - "
                    f"{sessao.dataHoraFim.strftime('%H:%M')}).",
                )

        return (False, "")

    def calcular_horas_alocadas_no_dia(self, usuario: Usuario, data: date) -> float:
        """
        Soma as horas de estudo já alocadas (AGENDADO ou CONCLUIDO) no dia informado.
        Sessões ATRASADO ou CANCELADO não consomem a cota de esforço do dia.
        """
        minutos = 0
        for sessao in usuario.sessoes:
            if (
                sessao.dataHoraInicio.date() == data
                and sessao.status in (StatusSessao.AGENDADO, StatusSessao.CONCLUIDO)
            ):
                minutos += sessao.obterDuracao()
        return minutos / 60.0

    def validarLimiteDiario(
        self,
        usuario: Usuario,
        data: date,
        horasAdicionais: float = 0.0,
    ) -> bool:
        """
        Valida a RN05 (Limite de Carga Diária): retorna True se a soma das horas já
        alocadas no dia + horasAdicionais não ultrapassar usuario.limiteHorasDiarias.
        """
        horas_atuais = self.calcular_horas_alocadas_no_dia(usuario, data)
        return (horas_atuais + horasAdicionais) <= (usuario.limiteHorasDiarias + 1e-9)

    # -------------------------------------------------------------------------
    # UC11 - Priorização por Prazo (RN03)
    # -------------------------------------------------------------------------

    def priorizarDisciplinas(self, disciplinas: List[Disciplina]) -> List[Disciplina]:
        """
        Ordena as disciplinas/metas segundo a RN03:
        1. Menor dataLimite (deadline mais próximo primeiro);
        2. Maior grauDificuldade (critério de desempate);
        3. Maior quantidade de horas pendentes.
        """
        return sorted(
            disciplinas,
            key=lambda d: (
                d.dataLimite,
                -d.grauDificuldade,
                -d.calcular_horas_pendentes_exatas(),
                d.nome.lower(),
            ),
        )

    def priorizarSessoes(
        self, usuario: Usuario, sessoes: List[SessaoEstudo]
    ) -> List[SessaoEstudo]:
        """Ordena uma lista de sessões com base na prioridade (RN03) de suas disciplinas."""

        def chave(sessao: SessaoEstudo) -> Tuple[date, int, datetime]:
            disc = usuario.obter_disciplina(sessao.disciplina_id)
            if disc is None:
                return (date.max, 0, sessao.dataHoraInicio)
            return (disc.dataLimite, -disc.grauDificuldade, sessao.dataHoraInicio)

        return sorted(sessoes, key=chave)

    # -------------------------------------------------------------------------
    # UC10 - Busca de Janelas Livres (RN01 + RN05 + Turnos Produtivos)
    # -------------------------------------------------------------------------

    @staticmethod
    def _arredondar_para_proximo_bloco(dt: datetime, passo_minutos: int = 30) -> datetime:
        """Arredonda um datetime para cima para o próximo múltiplo de passo_minutos."""
        dt = dt.replace(second=0, microsecond=0)
        resto = dt.minute % passo_minutos
        if resto != 0:
            dt += timedelta(minutes=(passo_minutos - resto))
        return dt

    @staticmethod
    def _subtrair_intervalos(
        base: Tuple[datetime, datetime],
        ocupados: List[Tuple[datetime, datetime]],
    ) -> List[Tuple[datetime, datetime]]:
        """Subtrai uma lista de intervalos ocupados de um intervalo base livre."""
        livres = [base]
        for oc_inicio, oc_fim in sorted(ocupados, key=lambda x: x[0]):
            proximos: List[Tuple[datetime, datetime]] = []
            for lv_inicio, lv_fim in livres:
                # Sem interseção
                if oc_fim <= lv_inicio or oc_inicio >= lv_fim:
                    proximos.append((lv_inicio, lv_fim))
                else:
                    # Sobra antes do ocupado
                    if oc_inicio > lv_inicio:
                        proximos.append((lv_inicio, oc_inicio))
                    # Sobra depois do ocupado
                    if oc_fim < lv_fim:
                        proximos.append((oc_fim, lv_fim))
            livres = proximos
        return livres

    def buscarJanelasLivres(
        self,
        usuario: Usuario,
        deadline: date,
        data_referencia: Optional[datetime] = None,
    ) -> List[dict]:
        """
        Implementa UC10 (Verificar Disponibilidade de Horários).
        Varre cada dia de `data_referencia` até `deadline` (inclusive), considerando:
        - Turnos produtivos do estudante;
        - Compromissos fixos (RN01);
        - Sessões de estudo já agendadas ou concluídas (RN01);
        - Limite máximo de horas diárias de estudo (RN05).
        """
        inicio_busca = self._arredondar_para_proximo_bloco(data_referencia or datetime.now())
        if inicio_busca.date() > deadline:
            return []

        turnos_ativos = usuario.turnosProdutivos or ["MANHA", "TARDE", "NOITE"]
        janelas: List[dict] = []

        dia_atual = inicio_busca.date()
        while dia_atual <= deadline:
            horas_alocadas = self.calcular_horas_alocadas_no_dia(usuario, dia_atual)
            capacidade_restante_horas = round(usuario.limiteHorasDiarias - horas_alocadas, 4)

            # Se o dia já atingiu o limite de carga diária (RN05), pula o dia
            if capacidade_restante_horas >= (BLOCO_MINIMO_MINUTOS / 60.0):
                ocupados: List[Tuple[datetime, datetime]] = []

                # Coleta compromissos fixos do dia (RN01)
                for comp in usuario.compromissos:
                    oc = comp.obter_ocorrencia_no_dia(dia_atual)
                    if oc is not None:
                        ocupados.append(oc)

                # Coleta sessões de estudo ativas do dia (RN01)
                for sessao in usuario.sessoes:
                    if (
                        sessao.dataHoraInicio.date() == dia_atual
                        and sessao.status in (StatusSessao.AGENDADO, StatusSessao.CONCLUIDO)
                    ):
                        ocupados.append((sessao.dataHoraInicio, sessao.dataHoraFim))

                # Analisa cada turno produtivo configurado pelo estudante
                for nome_turno in turnos_ativos:
                    if nome_turno not in FAIXAS_TURNOS:
                        continue
                    hora_ini, hora_fim = FAIXAS_TURNOS[nome_turno]
                    turno_inicio = datetime.combine(dia_atual, hora_ini)
                    turno_fim = datetime.combine(dia_atual, hora_fim)

                    # Não aloca no passado nem antes da referência
                    if turno_fim <= inicio_busca:
                        continue
                    if turno_inicio < inicio_busca:
                        turno_inicio = inicio_busca

                    trechos_livres = self._subtrair_intervalos((turno_inicio, turno_fim), ocupados)
                    for lv_inicio, lv_fim in trechos_livres:
                        duracao_min = int((lv_fim - lv_inicio).total_seconds() // 60)
                        if duracao_min >= BLOCO_MINIMO_MINUTOS:
                            janelas.append(
                                {
                                    "data": dia_atual,
                                    "inicio": lv_inicio,
                                    "fim": lv_fim,
                                    "duracao_minutos": duracao_min,
                                    "duracao_horas": round(duracao_min / 60.0, 2),
                                    "turno": nome_turno,
                                    "capacidade_restante_dia_horas": capacidade_restante_horas,
                                }
                            )

            dia_atual += timedelta(days=1)

        janelas.sort(key=lambda j: j["inicio"])
        return janelas

    # -------------------------------------------------------------------------
    # UC09 - Fragmentação de Sessão de Estudo (RN04 + RN05)
    # -------------------------------------------------------------------------

    def fragmentarSessao(
        self,
        sessao: SessaoEstudo,
        janelas: List[dict],
        usuario: Optional[Usuario] = None,
    ) -> List[SessaoEstudo]:
        """
        Implementa UC09 e RN04 (Fragmentação de Blocos).
        Divide a duração total da sessão em blocos menores (mínimo 30 min, preferencialmente
        múltiplos de 30 min) distribuídos pelas janelas livres disponíveis, sem nunca
        ultrapassar o limite diário de cada dia (RN05).
        """
        minutos_restantes = sessao.obterDuracao()
        blocos_criados: List[SessaoEstudo] = []

        # Controle local de minutos alocados por dia durante esta fragmentação (RN05)
        minutos_usados_por_dia: Dict[date, int] = {}

        for janela in janelas:
            if minutos_restantes < BLOCO_MINIMO_MINUTOS:
                break

            dia: date = janela["data"]
            inicio_janela: datetime = janela["inicio"]
            duracao_janela_min: int = int(janela["duracao_minutos"])

            # Calcula a cota diária disponível considerando alocações já feitas nesta execução
            if usuario is not None:
                horas_base = self.calcular_horas_alocadas_no_dia(usuario, dia)
                cap_dia_min = int(round((usuario.limiteHorasDiarias - horas_base) * 60))
            else:
                cap_horas = janela.get("capacidade_restante_dia_horas", duracao_janela_min / 60.0)
                cap_dia_min = int(round(cap_horas * 60))

            ja_usado_no_dia = minutos_usados_por_dia.get(dia, 0)
            cota_disponivel_dia_min = cap_dia_min - ja_usado_no_dia

            if cota_disponivel_dia_min < BLOCO_MINIMO_MINUTOS:
                continue

            # Tamanho máximo que cabe nesta janela e nesta cota diária
            alocavel_min = min(minutos_restantes, duracao_janela_min, cota_disponivel_dia_min)
            # Arredonda para baixo em múltiplos de 30 min se sobrar fração quebrada
            alocavel_min = (alocavel_min // BLOCO_MINIMO_MINUTOS) * BLOCO_MINIMO_MINUTOS

            if alocavel_min < BLOCO_MINIMO_MINUTOS:
                continue

            bloco_inicio = inicio_janela
            bloco_fim = bloco_inicio + timedelta(minutes=alocavel_min)

            # Checagem final de segurança com RN05 e RN01 quando o usuário está disponível
            if usuario is not None:
                if not self.validarLimiteDiario(
                    usuario, dia, (ja_usado_no_dia + alocavel_min) / 60.0
                ):
                    continue
                conflito, _ = self.verificarConflitoAgenda(usuario, bloco_inicio, bloco_fim)
                if conflito:
                    continue

            novo_bloco = SessaoEstudo(
                usuario_id=sessao.usuario_id,
                disciplina_id=sessao.disciplina_id,
                dataHoraInicio=bloco_inicio,
                dataHoraFim=bloco_fim,
                status=StatusSessao.AGENDADO,
                sessao_origem_id=sessao.id,
            )
            blocos_criados.append(novo_bloco)
            minutos_usados_por_dia[dia] = ja_usado_no_dia + alocavel_min
            minutos_restantes -= alocavel_min

        return blocos_criados

    # -------------------------------------------------------------------------
    # UC08 + UC12 - Acionar Reagendamento Completo
    # -------------------------------------------------------------------------

    def acionarReagendamento(
        self,
        usuario: Usuario,
        sessao: SessaoEstudo,
        data_referencia: Optional[datetime] = None,
    ) -> dict:
        """
        Implementa UC08 (Reagendar Sessão de Estudo) integrando UC09, UC10, UC11 e UC12.
        Retorna um relatório completo contendo as novas sessões, o log de decisão da IA
        e a notificação pronta para apresentação ao estudante.
        """
        log: List[str] = []

        # Garante que a sessão atrasada esteja marcada corretamente (RN02)
        if sessao.status != StatusSessao.ATRASADO:
            sessao.alterarStatus(StatusSessao.ATRASADO)
            log.append("RN02: Sessão marcada automaticamente como ATRASADO.")

        disciplina = usuario.obter_disciplina(sessao.disciplina_id)
        if disciplina is None:
            raise ValueError("Disciplina vinculada à sessão não encontrada no perfil do estudante.")

        # UC11 / RN03 — Priorização por Deadline e Grau de Dificuldade
        disciplinas_ordenadas = self.priorizarDisciplinas(usuario.disciplinas)
        ranking_nomes = [
            f"{idx + 1}º {d.nome} (Deadline: {d.dataLimite.strftime('%d/%m/%Y')}, "
            f"Dificuldade: {d.grauDificuldade}/5)"
            for idx, d in enumerate(disciplinas_ordenadas)
        ]
        posicao_prioridade = next(
            (i + 1 for i, d in enumerate(disciplinas_ordenadas) if d.id == disciplina.id),
            1,
        )
        log.append(
            f"RN03 (UC11): '{disciplina.nome}' classificada em {posicao_prioridade}º lugar "
            f"por deadline ({disciplina.dataLimite.strftime('%d/%m/%Y')}) e dificuldade "
            f"({disciplina.grauDificuldade}/5)."
        )

        # Define ponto inicial de busca: nunca no passado e nunca antes da sessão perdida
        agora = data_referencia or datetime.now()
        inicio_busca = max(agora, sessao.dataHoraFim)

        # UC10 / RN01 / RN05 — Busca de janelas livres
        janelas = self.buscarJanelasLivres(
            usuario=usuario,
            deadline=disciplina.dataLimite,
            data_referencia=inicio_busca,
        )
        log.append(
            f"RN01/RN05 (UC10): {len(janelas)} janela(s) livre(s) identificada(s) até "
            f"{disciplina.dataLimite.strftime('%d/%m/%Y')} respeitando compromissos fixos, "
            f"turnos produtivos ({', '.join(usuario.turnosProdutivos)}) e limite diário de "
            f"{usuario.limiteHorasDiarias}h."
        )

        minutos_originais = sessao.obterDuracao()
        horas_originais = minutos_originais / 60.0
        novas_sessoes: List[SessaoEstudo] = []
        fragmentada = False

        # Tenta primeiro alocar em uma janela contínua única (sem fragmentação)
        janela_continua = next(
            (
                j
                for j in janelas
                if j["duracao_minutos"] >= minutos_originais
                and self.validarLimiteDiario(usuario, j["data"], horas_originais)
            ),
            None,
        )

        if janela_continua is not None:
            nova_inicio = janela_continua["inicio"]
            nova_fim = nova_inicio + timedelta(minutes=minutos_originais)
            nova_sessao = SessaoEstudo(
                usuario_id=usuario.id,
                disciplina_id=sessao.disciplina_id,
                dataHoraInicio=nova_inicio,
                dataHoraFim=nova_fim,
                status=StatusSessao.AGENDADO,
                sessao_origem_id=sessao.id,
            )
            novas_sessoes = [nova_sessao]
            log.append(
                f"UC08: Janela contínua encontrada em {nova_inicio.strftime('%d/%m/%Y %H:%M')} - "
                f"{nova_fim.strftime('%H:%M')} ({horas_originais:.1f}h)."
            )
        else:
            # UC09 / RN04 — Sem janela contínua suficiente: aciona fragmentação de blocos
            log.append(
                f"RN04 (UC09): Nenhuma janela contínua de {horas_originais:.1f}h respeitando o "
                f"limite diário. Acionando fragmentação inteligente de blocos."
            )
            novas_sessoes = self.fragmentarSessao(sessao, janelas, usuario=usuario)
            fragmentada = len(novas_sessoes) > 1

        # Registra os novos blocos na agenda em memória do usuário
        for bloco in novas_sessoes:
            usuario.sessoes.append(bloco)
            if bloco not in disciplina.sessoes:
                disciplina.sessoes.append(bloco)

        minutos_reagendados = sum(b.obterDuracao() for b in novas_sessoes)
        minutos_nao_alocados = max(0, minutos_originais - minutos_reagendados)
        sucesso_total = minutos_reagendados == minutos_originais and minutos_originais > 0
        parcial = 0 < minutos_reagendados < minutos_originais

        # UC12 — Montagem da notificação detalhada para o estudante
        blocos_resumo = [
            {
                "id": str(b.id),
                "data": b.dataHoraInicio.strftime("%d/%m/%Y"),
                "dia_semana": self._nome_dia_semana(b.dataHoraInicio.weekday()),
                "inicio": b.dataHoraInicio.strftime("%H:%M"),
                "fim": b.dataHoraFim.strftime("%H:%M"),
                "duracao_horas": round(b.duracao_horas, 2),
                "duracao_minutos": b.obterDuracao(),
            }
            for b in novas_sessoes
        ]

        if sucesso_total:
            if fragmentada:
                titulo_notif = f"Sessão de {disciplina.nome} fragmentada e reagendada pela IA"
                descricao_blocos = "; ".join(
                    f"{b['dia_semana']} ({b['data']}) das {b['inicio']} às {b['fim']} ({b['duracao_horas']:.1f}h)"
                    for b in blocos_resumo
                )
                msg_notif = (
                    f"A sessão perdida de {disciplina.nome} ({horas_originais:.1f}h) foi "
                    f"fragmentada em {len(blocos_resumo)} blocos menores (RN04) respeitando seu "
                    f"limite de {usuario.limiteHorasDiarias}h/dia (RN05) antes do deadline "
                    f"({disciplina.dataLimite.strftime('%d/%m/%Y')}): {descricao_blocos}."
                )
            else:
                b = blocos_resumo[0]
                titulo_notif = f"Sessão de {disciplina.nome} reagendada pela IA"
                msg_notif = (
                    f"A sessão perdida de {disciplina.nome} ({horas_originais:.1f}h) foi "
                    f"realocada para {b['dia_semana']} ({b['data']}) das {b['inicio']} às "
                    f"{b['fim']}, sem conflitos (RN01) e dentro do limite diário (RN05)."
                )
        elif parcial:
            titulo_notif = f"Reagendamento parcial de {disciplina.nome}"
            descricao_blocos = "; ".join(
                f"{b['dia_semana']} ({b['data']}) das {b['inicio']} às {b['fim']} ({b['duracao_horas']:.1f}h)"
                for b in blocos_resumo
            )
            msg_notif = (
                f"Foram realocados {minutos_reagendados / 60:.1f}h de {horas_originais:.1f}h "
                f"em: {descricao_blocos}. Restaram {minutos_nao_alocados / 60:.1f}h sem janela "
                f"disponível antes do deadline ({disciplina.dataLimite.strftime('%d/%m/%Y')}) "
                f"dentro do limite diário de {usuario.limiteHorasDiarias}h."
            )
        else:
            titulo_notif = f"Não foi possível reagendar {disciplina.nome}"
            msg_notif = (
                f"Não há janelas livres suficientes antes do deadline "
                f"({disciplina.dataLimite.strftime('%d/%m/%Y')}) que respeitem seus compromissos "
                f"fixos (RN01) e o limite de {usuario.limiteHorasDiarias}h/dia (RN05). "
                f"Considere ajustar o prazo, os turnos produtivos ou o limite diário."
            )

        log.append(f"UC12: Notificação gerada — '{titulo_notif}'.")

        return {
            "sucesso": sucesso_total,
            "parcial": parcial,
            "fragmentada": fragmentada,
            "sessao_original_id": str(sessao.id),
            "disciplina": disciplina.nome,
            "deadline": disciplina.dataLimite.isoformat(),
            "prioridade_posicao": posicao_prioridade,
            "ranking_prioridades": ranking_nomes,
            "novas_sessoes": novas_sessoes,
            "minutos_originais": minutos_originais,
            "minutos_reagendados": minutos_reagendados,
            "minutos_nao_alocados": minutos_nao_alocados,
            "notificacao": {
                "titulo": titulo_notif,
                "mensagem": msg_notif,
                "blocos": blocos_resumo,
            },
            "log": log,
        }

    # -------------------------------------------------------------------------
    # RN02 - Gatilho Automático para Sessões Vencidas
    # -------------------------------------------------------------------------

    def verificarSessoesVencidas(
        self,
        usuario: Usuario,
        agora: Optional[datetime] = None,
    ) -> List[dict]:
        """
        Implementa o gatilho automático da RN02: identifica todas as sessões com status
        AGENDADO cujo horário de término já passou, ordena por prioridade (RN03) e
        aciona o reagendamento automático para cada uma.
        """
        referencia = agora or datetime.now()
        vencidas = [
            s
            for s in usuario.sessoes
            if s.status == StatusSessao.AGENDADO and s.dataHoraFim < referencia
        ]
        if not vencidas:
            return []

        # Prioriza as sessões vencidas pelo deadline mais próximo (RN03)
        vencidas_ordenadas = self.priorizarSessoes(usuario, vencidas)
        relatorios: List[dict] = []

        for sessao in vencidas_ordenadas:
            sessao.alterarStatus(StatusSessao.ATRASADO)
            relatorio = self.acionarReagendamento(
                usuario=usuario,
                sessao=sessao,
                data_referencia=referencia,
            )
            relatorio["gatilho"] = "AUTOMATICO_VENCIMENTO"
            relatorios.append(relatorio)

        return relatorios

    @staticmethod
    def _nome_dia_semana(weekday: int) -> str:
        nomes = [
            "Segunda-feira",
            "Terça-feira",
            "Quarta-feira",
            "Quinta-feira",
            "Sexta-feira",
            "Sábado",
            "Domingo",
        ]
        return nomes[weekday % 7]
