# Sistema de Reagendamento Inteligente — Protótipo RAD em Python

**Autores:** Davi Aires Bastos Medeiros e Yuri da Silva Araújo  
**Stack:** Python 3.10+ • Streamlit • SQLite • Pandas • Pytest

Assistente autônomo de gestão de tempo para estudantes que reorganiza sessões de estudo perdidas de forma realista, respeitando compromissos fixos, prazos (*deadlines*), fragmentação inteligente de blocos e limite máximo de carga diária (prevenção de *burnout*).

---

## Estrutura do Projeto

```text
02_RAD_Python_Codigo_Fonte/
├── app.py              # Interface visual em Streamlit (UC01 a UC12)
├── models.py           # Modelo de domínio OOP (Usuario, CompromissoFixo, Disciplina, SessaoEstudo, StatusSessao)
├── motor_ia.py         # Serviço inteligente MotorIA (RN01 a RN05 / UC08 a UC12)
├── database.py         # Persistência SQLite (CRUD completo + Seed do Fluxo Principal)
├── test_motor_ia.py    # Testes unitários com pytest validando RN01 a RN05
├── requirements.txt    # Dependências do projeto
└── README.md           # Este guia de instalação e uso
```

---

## Instalação e Execução

### 1. (Opcional) Crie e ative um ambiente virtual
```bash
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate
```

### 2. Instale as dependências
```bash
pip install -r requirements.txt
```

### 3. Execute a aplicação Streamlit
```bash
python -m streamlit run app.py
```

A interface abrirá automaticamente no navegador em `http://localhost:8501`.

### 4. Execute os testes unitários (RN01 a RN05)
```bash
python -m pytest -v
```

> **Dica (Windows):** Usar `python -m streamlit` e `python -m pytest` funciona mesmo quando a pasta `Scripts` do Python do usuário não está configurada no `PATH`.

---

## Como Testar o Fluxo Principal (Cenário do MRDS)

1. Na barra lateral esquerda, clique em **🚀 Carregar Cenário de Exemplo**.
   - O sistema cria o estudante **Davi Aires Medeiros** (limite de `3h/dia`), os compromissos fixos da semana (Trabalho, Faculdade e Reunião na quarta-feira), a disciplina **Banco de Dados** (`4h` até sexta-feira) e dois blocos de `2h` na terça e na quinta-feira.
2. Na aba **📅 Agenda & IA**, localize a sessão de **Banco de Dados** de **Terça-feira (14:00 às 16:00)**.
3. Clique em **⚠️ Tive um Imprevisto** e confirme a ação (**UC07**).
4. O **Motor de IA** entrará em ação automaticamente (**UC08–UC12**):
   - Prioriza **Banco de Dados** em #1 por ter o deadline mais próximo (**RN03**);
   - Identifica que Quarta-feira possui apenas `1h` livre (`17:00–18:00`) sem conflito com compromissos fixos (**RN01**);
   - Verifica que Quinta-feira já possui `2h` agendadas e limite diário de `3h`, restando `1h` de cota (**RN05**);
   - **Fragmenta** o bloco perdido de `2h` em dois blocos de `1h` (Quarta e Quinta) (**RN04**) e exibe a notificação detalhada com o log completo da decisão (**UC12**).
5. Você também pode clicar em **⏱️ Simular Sessão Vencida (RN02)** na barra lateral para observar o **gatilho automático** reagendando sessões cujo horário já passou.
