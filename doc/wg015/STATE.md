# WG015 port — orchestration state

Цель: порт bitbang USB стека + бутлоадера на К1921ВГ015 (НИИЭТ, RISC-V), код во флеше.
Ветка: claude/wg015-bitbang-usb-port-bxuu7w

## Фаза 0 — сбор информации (текущая)
- [ ] chip_info.md — верифицированные факты по К1921ВГ015 (агент: web-research)
- [x] stack_portability.md — инвентарь чип-зависимостей rv003usb + bootloader (готово; 10 главных препятствий в конце файла)
- [x] branch_notes.md — уроки порта py32 + анализ rx-tx-branchless улучшений (готово; вывод: база порта — master, branchless = cycle-exact артефакт CH32V003)
- [ ] PLAN.md — детальный план порта (пишет оркестратор после ревью трёх файлов выше)

## Фаза 1 — план (ультракод)
- [x] research/ — 6 майнеров по РП+SDK (готово, закоммичено)
- [x] PLAN.md draft v1 (закоммичен)
- [x] Red-team: линзы timing + flash → redteam_findings.md (2 blocker/major-набора)
- [ ] Red-team: линзы boot / arch / complete — УПАЛИ на лимите сессии (reset 10:30pm UTC).
      Возобновление: Workflow({scriptPath: "/root/.claude/projects/-home-user-rv003usb/2cc76999-0266-5060-b0a5-13e0eb56e9cd/workflows/scripts/vg015-plan-redteam-wf_8f218f26-7c7.js", resumeFromRunId: "wf_8f218f26-7c7"}) — timing/flash вернутся из кэша, три линзы отработают заново.
- [ ] research_bm310.md — агент по ядру CloudBEAR BM-310S6 упал на лимите; файл-скелет
      возможно частично записан; перезапустить исследование (вопросы в файле/истории:
      I-cache в ядре или во флеш-контроллере(CEN)? тайминги конвейера; mtvec vectored;
      sibling: Milandr MDR32F02 на BM-310S4; патч -mfix-cloudbear-0001).
- [x] PLAN.md v2 = интегрированы 18 находок timing+flash линз (blockers: z3-по-сайтово,
      G1-переписан с вытеснителем и кумулятивной экскурсией, H2 снята; majors: DPU-clobber,
      TX-slew R12, вход ≤55 тактов, PLIC threshold, MICC-бенч, LAT-integrity R11, z8-трамплины)
- [ ] research_bm310 перезапустить → при новых фактах точечно обновить PLAN §0/P1.4
- [ ] Red-team round 2: boot/arch/complete линзы (+ повторная верификация v2 при желании) →
      интеграция → PLAN v3 final

## Правила
- Каждый факт — с источником (URL / file:line / commit). Неподтверждённое — в раздел UNVERIFIED.
- Коммит + push после каждого готового файла.
- Контент share-ссылки получен текстом → shared_chat_notes.md. Чип подтверждён: К1921ВГ015.
- Директива: исполнение из ФЛЕШ первым (детерминизм важнее скорости), TCM — fallback.
