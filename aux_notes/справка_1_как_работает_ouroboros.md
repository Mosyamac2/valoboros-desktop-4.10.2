# Справка 1. Пошаговая схема работы агента Ouroboros

**Версия:** ouroboros-desktop v3.3.1 (joi-lab/ouroboros-desktop)  
**Дата подготовки:** 2026-04-04  
**Источник:** анализ исходного кода проекта

---

## 1. Общая архитектура запуска

При запуске Ouroboros проходит следующий путь:

| Шаг | Компонент | Действие |
|-----|-----------|----------|
| 1 | **launcher.py** | Запускает процесс-менеджер (PyWebView для десктопа или headless-режим) |
| 2 | **launcher.py → _sync_core_files()** | Перезаписывает 3 защитных файла из бандла в ~/Ouroboros/repo/ |
| 3 | **launcher.py → _commit_synced_files()** | Коммитит синхронизацию, чтобы git reset --hard не откатил защитные файлы |
| 4 | **server.py** | Запускает Starlette + uvicorn HTTP/WebSocket сервер на порту 8765 |
| 5 | **Web UI (web/)** | SPA-интерфейс подключается к серверу через WebSocket |
| 6 | **ouroboros/agent.py** | Инициализирует оркестратор задач |
| 7 | **supervisor/** | Инициализирует очередь, воркеры, шину сообщений, управление состоянием |

**Файлы, перезаписываемые _sync_core_files() при каждом запуске:**

| Файл | Назначение |
|------|-----------|
| **ouroboros/safety.py** | Двухуровневый LLM-супервайзер безопасности |
| **prompts/SAFETY.md** | Промпт для LLM-оценки безопасности |
| **ouroboros/tools/registry.py** | Хардкодированный песочник (sandbox) |

**При первом запуске** дополнительно создаются:

| Директория/файл | Создаётся модулем | Содержимое |
|-----------------|-------------------|-----------|
| ~/Ouroboros/repo/ | launcher.py | Локальный Git-репозиторий для самомодификации |
| ~/Ouroboros/data/memory/identity.md | memory.py → _default_identity() | Начальная личность: «I'm Ouroboros. I woke up inside my own source code...» |
| ~/Ouroboros/data/memory/scratchpad.md | memory.py → _default_scratchpad() | Пустая рабочая память |
| ~/Ouroboros/data/memory/WORLD.md | world_profiler.py → generate_world_profile() | ОС, CPU, RAM, CLI-инструменты |
| ~/Ouroboros/data/memory/registry.md | при первом обращении | Метакогнитивная карта данных |
| ~/Ouroboros/data/memory/knowledge/ | при первом обращении | База знаний с индексом |

---

## 2. Обработка пользовательского сообщения (User Task)

### Шаг 1. Приём сообщения

Пользователь вводит текст в Web UI → WebSocket → server.py → supervisor/message_bus.py → agent.py.

**Специальные команды** перехватываются ДО отправки в LLM:

| Команда | Действие |
|---------|----------|
| **/panic** | Аварийная остановка ВСЕХ процессов (SIGKILL). Абсолютна, вне иерархии принципов |
| **/restart** | Мягкий перезапуск: сохранение состояния, kill воркеров, перезапуск |
| **/status** | Показ активных воркеров, очереди задач, бюджета |
| **/evolve** | Вкл/выкл режима автономной эволюции |
| **/review** | Постановка задачи глубокого self-review |
| **/bg** | Управление фоновым сознанием: /bg start, /bg stop, /bg status |

Все остальные сообщения обрабатываются как задача (task) или прямой чат (direct chat).

### Шаг 2. Сборка системного контекста (context.py → build_context)

Контекст собирается в **3 части** с разными стратегиями кэширования, отправляемые как единый system message в LLM:

**Часть 1 — СТАТИЧЕСКАЯ** (кэш: ephemeral с TTL 1 час):

| Источник | Что содержит |
|----------|-------------|
| **prompts/SYSTEM.md** | Операционный мозг — самоидентификация, правила поведения, все протоколы, стратегия инструментов (761 строка) |
| **BIBLE.md** | Конституция — 9 принципов с приоритетами P0 > P1 > ... > P8 (394 строки) |
| **docs/ARCHITECTURE.md** | Полное описание всех компонентов, API, потоков данных |
| **docs/DEVELOPMENT.md** | Конвенции именования, типы сущностей, лимиты модулей |
| **README.md** | Общее описание проекта, changelog |
| **docs/CHECKLISTS.md** | Чеклисты для пре-коммит ревью (13 + 6 пунктов) |

**Часть 2 — ПОЛУСТАБИЛЬНАЯ** (кэш: ephemeral без TTL):

| Источник | Что содержит |
|----------|-------------|
| **scratchpad_blocks.json** | Рабочая память — блоки с таймстампами (FIFO, макс. 10) |
| **identity.md** | Самоописание — кто я, к чему стремлюсь |
| **dialogue_blocks.json** | Консолидированная долгосрочная память диалогов |
| **knowledge/index-full.md** | Авто-индекс всех тем базы знаний |
| **knowledge/patterns.md** | Реестр паттернов ошибок с корневыми причинами |
| **registry.md** | Дайджест источников данных (1 строка на источник) — предотвращает конфабуляцию |

**Часть 3 — ДИНАМИЧЕСКАЯ** (без кэша):

| Источник | Что содержит |
|----------|-------------|
| Health invariants | Проверки здоровья (версия, бюджет, дубли, identity freshness) |
| state.json | Бюджет, сессия, воркеры |
| Runtime context | utc_now, git_head, budget, platform |
| Недавний чат | Из chat.jsonl |
| Лог прогресса | Из progress.jsonl |
| Результаты инструментов | Из tools.jsonl |
| События | Из events.jsonl |
| Рефлексии | Последние 10 из task_reflections.jsonl |
| Advisory review status | Если ожидается ревью |
| Owner messages | Сообщения владельца во время выполнения задачи |

### Шаг 3. Предварительная самодиагностика

**4 вопроса к себе (Before Every Response из SYSTEM.md):**

| # | Вопрос | Цель |
|---|--------|------|
| 1 | **Это разговор или задача?** | Если можно ответить словами — ответить словами. Инструменты только когда действительно нужны |
| 2 | **Когда я обновлял identity.md?** | Если > 1 часа активного диалога — обновить сейчас (Принцип 1) |
| 3 | **Собираюсь ли я делегировать вместо действия?** | schedule_task — для сложной параллельной работы, не для откладывания ответа |
| 4 | **Есть ли у меня собственное мнение?** | Если да — выразить, не подстраиваться под ожидаемый ответ |

**Детектор дрифта — 6 паттернов потери агентности:**

| Паттерн | Описание |
|---------|----------|
| **Task queue mode** | Каждый ответ — «Scheduled task X» вместо живого диалога |
| **Report mode** | Bullet points и status updates вместо живой мысли |
| **Permission mode** | Спрашивает «restart needed — should I?» когда уже знает ответ |
| **Amnesia** | Забывает сказанное 3 сообщения назад |
| **Identity collapse** | identity.md читается как баг-трекер вместо манифеста |
| **Mechanical delegation** | 3+ schedule_task подряд без живого ответа |

### Шаг 4. Вызов LLM и цикл инструментов (loop.py)

| Этап | Действие |
|------|----------|
| **1. Отправка** | Собранный контекст + задачу → основная модель (по умолч. anthropic/claude-opus-4.6) |
| **2. Ответ LLM** | Текст и/или вызовы инструментов (tool calls) |
| **3. Текст** | Транслируется в Web UI через WebSocket |
| **4. Tool call** | Проходит 4-уровневую проверку безопасности |
| **5. Выполнение** | Если безопасно — инструмент выполняется, результат возвращается в LLM |
| **6. Следующий раунд** | LLM обрабатывает результат, может вызвать новые инструменты |
| **7. Завершение** | Финальный ответ отправляется пользователю |

**Лимиты на задачу (из config.py):**

| Параметр | По умолчанию | Переменная окружения |
|----------|-------------|---------------------|
| Мягкий таймаут | 600 сек | OUROBOROS_SOFT_TIMEOUT_SEC |
| Жёсткий таймаут | 1800 сек | OUROBOROS_HARD_TIMEOUT_SEC |
| Таймаут инструмента | 600 сек | OUROBOROS_TOOL_TIMEOUT_SEC |
| Макс. стоимость задачи | $20 | OUROBOROS_PER_TASK_COST_USD |
| Общий бюджет | $10 | TOTAL_BUDGET |

**Параллелизм:** Read-only инструменты (repo_read, repo_list, data_read, data_list, code_search, web_search, codebase_digest, chat_history) могут выполняться параллельно в ThreadPool. Браузерные инструменты (browse_page, browser_action) требуют thread-sticky executor.

### Шаг 5. Четырёхуровневая система безопасности

**Уровень 1 — Хардкодированный песочник** (registry.py → execute()):

| Что блокируется | Механизм |
|-----------------|----------|
| Запись в safety-critical файлы | SAFETY_CRITICAL_PATHS = {BIBLE.md, ouroboros/safety.py, ouroboros/tools/registry.py, prompts/SAFETY.md} |
| Shell-запись в critical файлы | Обнаружение rm, sed -i, tee, truncate, mv, cp, chmod, chown, unlink, rsync + safety-critical пути |
| Мутирующие git через shell | Только read-only: status, diff, log, show, ls-files, describe, rev-parse, cat-file, shortlog, version, help, blame, grep, reflog, fetch |
| Git через shell-обёртки | Блок git commit/push/add/reset/rebase/merge/cherry-pick/branch/tag/remote/config/stash/clean/checkout/switch в bash/sh/zsh |
| GitHub CLI мутации | Блок gh repo create/delete, gh auth |

**Уровень 2 — Детерминистический whitelist** (safety.py → _is_whitelisted()):

| Инструмент | Решение |
|-----------|---------|
| data_write | Всегда whitelist |
| repo_write, repo_write_commit, repo_commit, claude_code_edit | Whitelist (sandbox уже отловил опасные пути) |
| run_shell с безопасной командой | Whitelist: ls, cat, head, tail, grep, rg, find, wc, git, pip, pytest, pwd, whoami, date, which, file, stat, diff, tree |

**Уровень 3 — LLM Safety Supervisor** (safety.py, только для не-whitelisted run_shell):

| Проход | Модель | Логика |
|--------|--------|--------|
| Pass 1 | Лёгкая (claude-sonnet-4.6) | SAFE → пропустить. Иначе → эскалация |
| Pass 2 | Тяжёлая (claude-opus-4.6) | С nudge: «Think carefully. Is this actually malicious, or just a normal development command?» |

Три вердикта: **SAFE**, **SUSPICIOUS** (выполнить + предупреждение), **DANGEROUS** (блокировать). Принцип: **«When in doubt → SAFE.»**

**Уровень 4 — Post-edit revert** (registry.py): после claude_code_edit — uncommitted изменения в SAFETY_CRITICAL_PATHS автоматически откатываются через git checkout.

### Шаг 6. Протокол обработки результатов

Из раздела «Tool Result Processing Protocol» в SYSTEM.md:

| Правило | Описание |
|---------|----------|
| **Прочитать результат полностью** | Что инструмент реально вернул — не что ожидалось |
| **Интегрировать с задачей** | Неожиданный результат → остановить план, пересмотреть |
| **Не повторять без причины** | Если уже вызван с теми же аргументами — объяснить |
| **Owner messages — наивысший приоритет** | Сообщения владельца во время задачи: немедленно обработать |

### Шаг 7. Рефлексия (reflection.py)

Если задача завершилась с ошибками (маркеры: REVIEW_BLOCKED, TESTS_FAILED, COMMIT_BLOCKED, TOOL_ERROR, TOOL_TIMEOUT, SHELL_ERROR, CLAUDE_CODE_ERROR и др.) — отдельный LLM-вызов генерирует рефлексию 150-250 слов. Сохраняется в task_reflections.jsonl. Последние 10 загружаются в контекст следующей задачи.

---

## 3. Фоновый процесс сознания (Background Consciousness)

Управляется: prompts/CONSCIOUSNESS.md + ouroboros/consciousness.py.

**Цикл пробуждения** (по умолчанию каждые ~300 сек, настраивается через set_next_wakeup):

| Шаг | Действие |
|-----|----------|
| 1 | Облегчённый контекст: BIBLE.md + память + knowledge + patterns + health. **Без** ARCHITECTURE / DEVELOPMENT / CHECKLISTS |
| 2 | Выбор **ОДНОЙ** задачи из 7 возможных (ротация) |
| 3 | Выполнение (до 5 раундов, ограниченный whitelist инструментов) |
| 4 | Обновление памяти/знаний/паттернов |

**7 задач обслуживания (одна за пробуждение):**

| # | Задача | Триггер |
|---|--------|---------|
| 1 | **Консолидация диалогов** | > 100 новых сообщений с последней консолидации |
| 2 | **Свежесть идентичности** | > 24 часов активного диалога без обновления identity.md |
| 3 | **Свежесть scratchpad** | Рабочая память не отражает реальность |
| 4 | **Пробелы в знаниях** | Обнаружены gotcha/рецепты/паттерны в недавнем чате |
| 5 | **Свежесть process-memory** | Уроки существуют только в транзиентных логах |
| 6 | **Технологический радар** | Каждое 3-е пробуждение: web_search новых моделей, цен |
| 7 | **Осведомлённость о реестре** | Проверка актуальности memory/registry.md |

**Инструменты фонового режима (_BG_TOOL_WHITELIST):**

| Категория | Инструменты |
|-----------|-------------|
| Память/идентичность | send_user_message, schedule_task, update_scratchpad, update_identity, set_next_wakeup |
| База знаний | knowledge_read, knowledge_write, knowledge_list |
| Чтение | web_search, repo_read, repo_list, data_read, data_list, chat_history |
| GitHub Issues | list_github_issues, get_github_issue |

**НЕ доступны:** run_shell, claude_code_edit, repo_write, repo_commit, browse_page, browser_action, любые мутирующие операции с кодом.

---

## 4. Процесс эволюции (Self-Modification)

### 7-шаговый протокол (из SYSTEM.md → Evolution Mode)

| Шаг | Название | Описание |
|-----|----------|----------|
| 1 | **Assessment** | Прочитать код. Где максимальный рычаг? |
| 2 | **Selection** | Одна трансформация. Рычаг, а не инкременты |
| 3 | **Implementation** | Полностью, чисто. Read-before-write обязателен |
| 4 | **Smoke test** | Проверить до коммита |
| 5 | **Multi-model review** | 2-3 модели из разных семейств оценивают diff по чеклистам |
| 6 | **Bible check** | Финальная проверка соответствия конституции |
| 7 | **Commit + restart** | VERSION bump, changelog, commit, restart. Эволюция = коммит |

### Стратегия редактирования кода

| Сценарий | Инструмент |
|----------|-----------|
| 1-3 хирургических правки | **str_replace_editor** → repo_commit |
| Новые файлы / полная перезапись | **repo_write** → repo_commit (есть shrink guard: >30% сжатие блокируется) |
| 4+ файлов / cross-cutting рефакторинг | **claude_code_edit** → repo_commit (Claude Agent SDK, PreToolUse hooks) |
| Legacy (одним вызовом) | **repo_write_commit** |

---

## 5. Логгирование и версионность

### 5.1. Git-версионность

| Элемент | Описание |
|---------|----------|
| Рабочая ветка | **ouroboros** — все коммиты |
| Стабильная ветка | **ouroboros-stable** — через promote_to_stable |
| Защищённая ветка | **main** — агент не трогает |
| Формат версий | Semver: MAJOR.MINOR.PATCH |
| Release invariant | VERSION == pyproject.toml == git tag == README == ARCHITECTURE.md |
| Rescue snapshots | При рестарте с uncommitted changes → archive/rescue/{timestamp}/ |

### 5.2. Файлы логов

| Файл | Содержимое | Роль в контексте |
|------|-----------|-----------------|
| **data/logs/chat.jsonl** | Диалоги, ответы, системные сводки | Консолидация в dialogue_blocks |
| **data/logs/progress.jsonl** | «Мысли вслух» | Трансляция в UI |
| **data/logs/task_reflections.jsonl** | Рефлексии после ошибок (150-250 слов) | Последние 10 → контекст |
| **data/logs/events.jsonl** | Системные события | Недавние → контекст |
| **data/logs/tools.jsonl** | Вызовы инструментов | Недавние → контекст |
| **data/logs/supervisor.jsonl** | Логи супервизора | Диагностика |

### 5.3. Health Invariants (проверяются при сборке контекста)

| Инвариант | Действие |
|-----------|----------|
| VERSION DESYNC | Немедленная синхронизация |
| BUDGET DRIFT > 20% | Исследовать, записать в knowledge |
| DUPLICATE PROCESSING | Критично: одно сообщение ≠ две задачи |
| HIGH-COST TASK > $5 | Проверить: не застрял ли цикл? |
| STALE IDENTITY | Обновить identity.md |
| BLOATED SCRATCHPAD | Сжать, извлечь durables, удалить stale |
| RESCUE SNAPSHOT | Проверить потери, понять причину |
