-- ============================================================================
-- Sistema de Reagendamento Inteligente
-- Script DDL Completo do Banco de Dados Relacional (SQLite / ANSI SQL)
-- Autores: Davi Aires Bastos Medeiros e Yuri da Silva Araújo
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- 1. Tabela: usuarios (Entidade Usuario / Perfil)
-- Suporta UC01 e RN05 (limite_horas_diarias)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usuarios (
    id TEXT PRIMARY KEY,                                  -- UUID v4
    nome TEXT NOT NULL CHECK (length(trim(nome)) > 0),
    email TEXT NOT NULL UNIQUE CHECK (instr(email, '@') > 1),
    limite_horas_diarias INTEGER NOT NULL DEFAULT 4
        CHECK (limite_horas_diarias >= 1 AND limite_horas_diarias <= 24),
    turnos_produtivos TEXT NOT NULL DEFAULT '["MANHA","TARDE","NOITE"]', -- JSON list[str]
    criado_em TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    atualizado_em TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ----------------------------------------------------------------------------
-- 2. Tabela: compromissos_fixos (Entidade CompromissoFixo / Rotina)
-- Suporta UC02 e RN01 (zonas proibidas para agendamento de estudos)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS compromissos_fixos (
    id TEXT PRIMARY KEY,                                  -- UUID v4
    usuario_id TEXT NOT NULL,
    titulo TEXT NOT NULL CHECK (length(trim(titulo)) > 0),
    data_hora_inicio TEXT NOT NULL,                       -- ISO-8601 (YYYY-MM-DDTHH:MM:SS)
    data_hora_fim TEXT NOT NULL,                          -- ISO-8601 (YYYY-MM-DDTHH:MM:SS)
    recorrente INTEGER NOT NULL DEFAULT 0 CHECK (recorrente IN (0, 1)),
    CHECK (data_hora_fim > data_hora_inicio),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 3. Tabela: disciplinas (Entidade Disciplina / Meta de Estudo)
-- Suporta UC03 e RN03 (priorização por data_limite e grau_dificuldade)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS disciplinas (
    id TEXT PRIMARY KEY,                                  -- UUID v4
    usuario_id TEXT NOT NULL,
    nome TEXT NOT NULL CHECK (length(trim(nome)) > 0),
    carga_horaria_total INTEGER NOT NULL CHECK (carga_horaria_total > 0),
    data_limite TEXT NOT NULL,                            -- ISO-8601 Date (YYYY-MM-DD)
    grau_dificuldade INTEGER NOT NULL DEFAULT 3
        CHECK (grau_dificuldade >= 1 AND grau_dificuldade <= 5),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 4. Tabela: sessoes_estudo (Entidade SessaoEstudo)
-- Suporta UC04, UC05, UC06, UC07, UC08, UC09 e RN01..RN05
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessoes_estudo (
    id TEXT PRIMARY KEY,                                  -- UUID v4
    usuario_id TEXT NOT NULL,
    disciplina_id TEXT NOT NULL,
    data_hora_inicio TEXT NOT NULL,                       -- ISO-8601 (YYYY-MM-DDTHH:MM:SS)
    data_hora_fim TEXT NOT NULL,                          -- ISO-8601 (YYYY-MM-DDTHH:MM:SS)
    status TEXT NOT NULL DEFAULT 'AGENDADO'
        CHECK (status IN ('AGENDADO', 'CONCLUIDO', 'ATRASADO', 'CANCELADO')),
    sessao_origem_id TEXT NULL,                           -- Referência à sessão atrasada original (RN04)
    CHECK (data_hora_fim > data_hora_inicio),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
    FOREIGN KEY (disciplina_id) REFERENCES disciplinas(id) ON DELETE CASCADE,
    FOREIGN KEY (sessao_origem_id) REFERENCES sessoes_estudo(id) ON DELETE SET NULL
);

-- ----------------------------------------------------------------------------
-- 5. Tabela: notificacoes (Suporte a UC12 - Enviar Notificação de Reagendamento)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notificacoes (
    id TEXT PRIMARY KEY,                                  -- UUID v4
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

-- ----------------------------------------------------------------------------
-- 6. Índices para otimização de consultas de agenda, conflitos e MotorIA
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_compromissos_usuario_periodo
    ON compromissos_fixos (usuario_id, data_hora_inicio, data_hora_fim);

CREATE INDEX IF NOT EXISTS idx_disciplinas_usuario_deadline
    ON disciplinas (usuario_id, data_limite, grau_dificuldade DESC);

CREATE INDEX IF NOT EXISTS idx_sessoes_usuario_periodo
    ON sessoes_estudo (usuario_id, data_hora_inicio, data_hora_fim);

CREATE INDEX IF NOT EXISTS idx_sessoes_usuario_status
    ON sessoes_estudo (usuario_id, status);

CREATE INDEX IF NOT EXISTS idx_sessoes_disciplina_status
    ON sessoes_estudo (disciplina_id, status);

CREATE INDEX IF NOT EXISTS idx_notificacoes_usuario_criado
    ON notificacoes (usuario_id, criado_em DESC);

-- ----------------------------------------------------------------------------
-- 7. View auxiliar: Progresso das Disciplinas (Horas Concluídas vs. Pendentes)
-- ----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_progresso_disciplinas AS
SELECT
    d.id AS disciplina_id,
    d.usuario_id,
    d.nome AS disciplina_nome,
    d.carga_horaria_total,
    d.data_limite,
    d.grau_dificuldade,
    COALESCE(
        SUM(
            CASE
                WHEN s.status = 'CONCLUIDO' THEN
                    (julianday(s.data_hora_fim) - julianday(s.data_hora_inicio)) * 24.0
                ELSE 0.0
            END
        ),
        0.0
    ) AS horas_concluidas,
    MAX(
        0.0,
        d.carga_horaria_total - COALESCE(
            SUM(
                CASE
                    WHEN s.status = 'CONCLUIDO' THEN
                        (julianday(s.data_hora_fim) - julianday(s.data_hora_inicio)) * 24.0
                    ELSE 0.0
                END
            ),
            0.0
        )
    ) AS horas_pendentes
FROM disciplinas d
LEFT JOIN sessoes_estudo s ON s.disciplina_id = d.id
GROUP BY d.id;
