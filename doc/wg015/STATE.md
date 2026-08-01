# WG015 port — orchestration state

Цель: порт bitbang USB стека + бутлоадера на К1921ВГ015 (НИИЭТ, RISC-V BM-310S6),
код во флеше (flash-first), бескварцевость — целевой параллельный трек CLK-B.
Ветка: claude/wg015-bitbang-usb-port-bxuu7w

## Фаза 0 — сбор информации: ЗАВЕРШЕНА
- [x] chip_info.md (+CORRECTIONS 1-11) — верифицированные факты по К1921ВГ015
- [x] stack_portability.md — инвентарь чип-зависимостей rv003usb + bootloader
- [x] branch_notes.md — уроки py32; вывод: база порта — master
- [x] shared_chat_notes.md — конспект исследовательского чата пользователя
- [x] research/ — 7 файлов: flash, clocks, gpio, core_irq, usb_power, errata, bm310

## Фаза 1 — план (ультракод): ЗАВЕРШЕНА
- [x] PLAN.md v1 → red-team (5 линз, 2 раунда, wf_8f218f26-7c7) → v2 → v2.2 → **v3 final draft**
- [x] redteam_findings.md — 42 находки двух раундов, все blocker/major интегрированы
- [x] research_bm310.md — I-cache в ядре (неотключаем, только fence.i); TCM двухпортовый
      0-wait; флеш-D-load hazard доказан (-mfix-cloudbear-0001)
- Ключевые решения v3: макро-контракты вместо #if-колонок (третий чип = новый хедер);
  exit-to-app контракт лодера; адрес-гвард блобов + пейсинг CLI; LOCKSET снят;
  DBG0=B2 (DPU-режим — опция); 4-й шов REBOOT_TO_BOOTLOADER; бит-идентичность V003+V00x;
  G1 с вытеснителем и кумулятивной экскурсией; LAT=1 c integrity-гейтом.

## Фаза 2 — имплементация по статической аналитике (ТЕКУЩАЯ)
Директива: код пишется сейчас, железо — только посттюнинг (TUNE-ручки + бенчи).
- [x] **rv003usb.S параметризован под WG015** (коммит fb72370): ключевой ход — окно
      MASKLB (base+0x400+(USB_DMASK<<2)) = точный BSHR-эквивалент (абсолютная маск.
      запись только D±, DPU не трогается) → BSHR_OFFSET переопределён, s1/t1-семантика
      TX сохранена, поток инструкций почти не изменился. PLIC claim/complete ack,
      rdcycle keepalive (актуатор трима = пусто), USB_FAR_DISPATCH (унификация V00x
      far-call для TCM-фолбэка), .timecrit секция, P10-sink (MASKLB[0] = no-op).
- [x] **Машинная верификация** (gcc-riscv64-unknown-elf 13.2 в контейнере,
      scratchpad/bitcheck): V003 сборка БИТ-ИДЕНТИЧНА, V00x сборка БИТ-ИДЕНТИЧНА,
      WG015-ветка ассемблируется, .timecrit = 960 Б (<< 2К I-cache — H1 усилена),
      кодировки MASKLB/rdcycle/OUTEN/INTSTATUS проверены дизассемблером.
- [ ] Скелет таргета rv003usb/wg015/ (агент в фоне: шим ch32fun.h, K1921VG015_min.h,
      startup, ld×2, Makefile.wg015)
- [ ] TUNE-паддинги: леджер слотов WG015 (первый статический проход) — после скелета
- [ ] C-слой: usb_setup WG015-ветка, REBOOT_TO_BOOTLOADER, demo_hidapi конфиг
- [ ] Бутлоадер + хост-CLI; бенч-прошивки wg015_bench
- [ ] Трек BOOT-B (после P4, отдельный бранч): DFU-лодер на базе
      kimstik/SAMDx1-USB-DFU-Bootloader — см. PLAN Р8/BOOT-B. dfu-util вместо CLI,
      bwPollTimeout вместо пейсинга. Репо добавить через add_repo при старте трека.

## Далее — P0/P1 (стенд): ТРЕБУЕТ ЖЕЛЕЗА (только валидация и тюнинг)
Открытых вопросов к пользователю нет — всё в дефолтах (PLAN §7).

## Правила процесса
- Каждый факт — с источником; неподтверждённое — в UNVERIFIED/GAPS.
- Коммит + push после каждого шага; STATE.md ведётся.
- Директива: флеш первым (детерминизм важнее скорости), TCM — измеримо обоснованный отход.
