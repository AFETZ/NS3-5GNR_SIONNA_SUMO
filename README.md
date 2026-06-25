# 1548-CAVISE2026\_5GNR

![Portfolio](https://img.shields.io/badge/portfolio-thesis_research-2f6f6d?style=flat-square)
![ns-3](https://img.shields.io/badge/simulator-ns--3-44546a?style=flat-square)
![5G NR-V2X](https://img.shields.io/badge/network-5G_NR--V2X-3b6ea8?style=flat-square)
![SUMO](https://img.shields.io/badge/mobility-SUMO-7a5c2e?style=flat-square)
![Sionna](https://img.shields.io/badge/ray_tracing-Sionna_RT-2f6f6d?style=flat-square)

### Выпускная квалификационная работа

> **«Влияние потерь сообщений 5G NR-V2X Mode 2 на безопасность подключённых автономных транспортных средств»**
>
> Исследовательский overlay поверх [ms-van3t](https://github.com/ms-van3t-devs/ms-van3t) / [VaN3Twin](https://github.com/DriveX-devs/NEWWAY): co-simulation ns-3 + SUMO + NVIDIA Sionna для изучения причинно-следственной цепочки потеря CAM → деградация решений → дорожный инцидент.
>
> **Автор:** Физулин Андрей Владимирович · HSE, JSC AVTOVAZ
> **Лицензия:** GPL-2.0 (наследует от ms-van3t)

<p align="center">
  <img src="img/VaN3Twin_logo_v5_blk.png" height="36" alt="VaN3Twin"/>
  &nbsp;&nbsp;+&nbsp;&nbsp;
  <img src="img/MS-VAN3T_logo-V2_small.png" height="36" alt="ms-van3t"/>
</p>

---

## Результаты

### Причинно-следственная цепочка: потеря CAM → авария

![Causal chain](img/results/cross_layer_causal_chain.png)

*Кросс-слоевая цепочка: деградация физического канала (SNR ↓) → рост потерь CAM (PRR ↓) → запоздалое обнаружение застрявшего лидера → столкновение. Прогон `truck_lane_change`, 2026-03-20.*

### Поведение транспортных средств при разных PRR

![Behavioral dashboard](img/results/behavioral_dashboard.png)

*Скорости и манёвры veh3–veh5 при высоком (safe) и низком (crash) PRR. При PRR < 40% veh4 не успевает перестроиться.*

### PRR: кумулятивное распределение

![PRR cumulative](img/results/intuitive_prr_cumulative.png)

*CDF пакетного приёма по прогонам: разрыв между safe (синий) и crash (красный) сценарием — ключевое evidence ВКР.*

### Drop CAM → решение водителя

![Drop vs control](img/results/emergency_drop_vs_control_actions.png)

*Корреляция между потерянными пакетами и управляющими действиями: каждый drop в критическом окне увеличивает риск инцидента.*

---

## Что это такое

1548-CAVISE2026_5GNR — это **надстройка** над фреймворком ms-van3t/VaN3Twin, которая:

- добавляет **оригинальные сценарии** для исследования причинно-следственной цепочки: потеря CAM → ухудшение принятия решений → дорожный инцидент;
- интегрирует **NVIDIA Sionna** (ray-tracing канал) в петлю обратной связи ns-3 ↔ SUMO;
- предоставляет **инструменты постобработки** (графики PRR, временны́е шкалы drop → decision, аудит логов);
- хранит **воспроизводимые evidence-прогоны** для защиты ВКР.

Базовый фреймворк (ns-3, TraCI-SUMO, ETSI ITS-G5/NR-V2X стек, CARLA-расширение, DCC) полностью наследуется от ms-van3t/VaN3Twin — мы не переписываем то, что уже работает.

---

## Структура репозитория

```
NEWWAY/
├── experiments/          # Все сценарии и эксперименты
│   ├── truck_lane_change/        # ВКР: смена полосы — PRR → манёвр
│   ├── intersection_crash/       # ВКР: конфликт на перекрёстке
│   ├── intersection_radar_comm/  # Радар + V2X comm, sweep по мощности
│   ├── cpm_perception/           # CPM / коллективное восприятие
│   ├── compare_tech/             # Сравнение NR-V2X vs 802.11p
│   ├── intersection_v2x_awareness/ # Переработанный intersection (apr 2026)
│   ├── operational/              # Стандартные примеры ms-van3t
│   ├── strict_sionna_vkr/        # Строгий Sionna-пакет с manifests
│   └── raw/                      # Сырые прогоны для воспроизводимости
│
├── runs/                 # Evidence-прогоны по датам (YYYY-MM-DD)
├── tools/                # Аналитические скрипты и генераторы
│   ├── plots/            # Графики (PRR, drop timeline, story plots)
│   ├── analysis/         # Аудит логов, агрегация результатов
│   ├── vkr/              # Генераторы фигур и сборка docx/pdf ВКР
│   └── scenario_manager/ # Централизованный запуск / sweep
│
├── reports/              # Отчёты по циклам практики
├── conference/           # Финальные версии статей (docx)
├── src/                  # Overlay C++ модулей ns-3 (от ms-van3t + доработки)
├── scripts/              # Operational helpers (sync, docker)
├── archive/              # Замороженные тексты ВКР, дубликаты — НЕ для ИИ
│
├── AGENTS.md             # Инструкции для ИИ-агентов
├── REPO_LAYOUT.md        # Полная карта репозитория
└── DEVELOPMENT.md        # Checklist setup/configure/build/test
```

Подробная карта — в [`REPO_LAYOUT.md`](REPO_LAYOUT.md).

---

## Быстрый старт

### 1. Установка зависимостей и сборка

```bash
# SUMO
sudo add-apt-repository ppa:sumo/stable
sudo apt update && sudo apt install sumo sumo-tools sumo-doc

# Клонировать и собрать ns-3-dev с overlay
git clone https://github.com/AFETZ/1548-CAVISE2026_5GNR.git
cd NEWWAY
./sandbox_builder.sh install-dependencies   # первый раз
# или ./sandbox_builder.sh                  # если ns-3 уже установлен

# Перейти в ns-3-dev и собрать
cd .bootstrap-ns3/ns-3-dev   # или куда sandbox поместил ns-3
./ns3 configure --build-profile=optimized --enable-examples --enable-tests --disable-python --disable-werror
./ns3 build
```

Подробный checklist с типичными ошибками — в [`DEVELOPMENT.md`](DEVELOPMENT.md).

### 2. Запустить ВКР-сценарий (lane change)

```bash
# Из корня репозитория
experiments/truck_lane_change/scripts/run.sh
```

Сценарий автоматически:
- запустит SUMO + ns-3 симуляцию;
- (опционально) поднимет локальный Sionna-сервер;
- сохранит результаты в `~/NEWWAY_runs/<дата>/truck_lane_change/`;
- построит графики PRR → манёвр.

### 3. Запустить intersection-сценарий

```bash
experiments/intersection_crash/scripts/run.sh
```

### 4. Постобработка прогона

```bash
# Story-графики (дипломный стиль)
./.venv/bin/python tools/plots/build_valid_scenario_story_plots.py \
  --run-dir runs/2026-03-04/<run_dir>

# Временна́я шкала drop → decision
./.venv/bin/python tools/plots/build_drop_decision_timeline.py \
  --run-dir runs/2026-03-04/<run_dir>

# Полный аудит логов
./.venv/bin/python tools/analysis/analyze_all_logs.py \
  --root runs --out-dir runs --tag 2026-03-04
```

---

## Основные сценарии ВКР

### `truck_lane_change` — смена полосы при потере CAM

Демонстрирует причинно-следственную цепочку:
**потеря CAM** (низкое SNR / перегрузка канала) → **запоздалое обнаружение застрявшего лидера** → **либо успешный объезд (high PRR), либо столкновение (low PRR)**.

```bash
experiments/truck_lane_change/scripts/run.sh

# Варианты:
USE_SIONNA=0 experiments/truck_lane_change/scripts/run.sh   # без ray-tracing
USE_SIONNA=1 experiments/truck_lane_change/scripts/run.sh   # с Sionna (нужен сервер)
```

Результаты: `~/NEWWAY_runs/<дата>/truck_lane_change/`  
Артефакты ВКР: `runs/2026-03-*/`

### `intersection_crash` — конфликт на перекрёстке

Третий автомобиль нарушает приоритет при деградации V2X-канала.

```bash
experiments/intersection_crash/scripts/run.sh
```

### `intersection_radar_comm` — радар + V2X comm

Три режима: только радар / только V2X comm / комбинированный. Sweep по `equiv_tx_power`.

```bash
experiments/intersection_radar_comm/scripts/run.sh
experiments/intersection_radar_comm/scripts/run_radar_bad_link.sh
experiments/intersection_radar_comm/scripts/run_radar_good_link.sh
```

### `strict_sionna_vkr` — строгий Sionna-прогон

Полный набор manifests, сцен и скриптов для воспроизводимых Sionna-прогонов.

```bash
experiments/strict_sionna_vkr/scripts/start_sionna_server.sh  # на GPU-машине
experiments/strict_sionna_vkr/scripts/run_native_metrics.sh   # основной прогон
```

---

## Sionna ray-tracing

[NVIDIA Sionna](https://nvlabs.github.io/sionna/) используется как физический канальный движок вместо стандартной log-distance модели.

```bash
# Установка
pip install sionna   # v0.19.0 или v1.0

# Запуск сервера (отдельный терминал / GPU-машина)
./.venv_sionna/bin/python sionna_v1_server_script.py \
  --file_name src/sionna/scenarios/SionnaCircleScenario/scene.xml \
  --local-machine --verbose

# Затем запуск сценария с Sionna
USE_SIONNA=1 SIONNA_SERVER_IP=127.0.0.1 \
  experiments/truck_lane_change/scripts/run.sh
```

Сцены в формате Mitsuba XML создаются в Blender 3.6.22 с аддоном [mitsuba-blender](https://github.com/mitsuba-renderer/mitsuba-blender).

---

## Операционные примеры (от ms-van3t)

В `experiments/operational/` лежат стандартные примеры ms-van3t без модификаций:

| Пример | Команда ns-3 |
|---|---|
| NR-V2X emergency vehicle alert | `./ns3 run "v2v-emergencyVehicleAlert-nrv2x"` |
| V2V CAM exchange + Sionna | `./ns3 run "v2v-cam-exchange-sionna-nrv2x"` |
| 802.11p + NR-V2X coexistence | `./ns3 run "v2v-coexistence-80211p-nrv2x"` |
| CTTC NR-V2X demo | `./ns3 run "cttc-nr-v2x-demo-simple"` |
| West-to-east highway | `./ns3 run "nr-v2x-west-to-east-highway"` |
| 5G PHY metrics | `./ns3 run "5g-phy-metrics"` |

Запускать из директории ns-3-dev (`.bootstrap-ns3/ns-3-dev`).  
Документация ms-van3t: [ms-van3ts-documentation.readthedocs.io](https://ms-van3ts-documentation.readthedocs.io/en/master/)

---

## Отношение к ms-van3t и VaN3Twin

```
ms-van3t (Politecnico di Torino / Milano)
    └── VaN3Twin (+ NVIDIA Sionna ray-tracing)
            └── NEWWAY overlay (Физулин А.В., ВКР 2026)
                    ├── src/automotive/  — доработки EVA, emergencyVehicleAlert
                    ├── src/sionna/      — расширение Sionna connection handler
                    └── experiments/     — оригинальные ВКР-сценарии
```

Весь код в `src/` является форком/overlay от upstream ms-van3t/VaN3Twin.  
Оригинальная работа сосредоточена в `experiments/`, `tools/`, и точечных изменениях в `src/automotive/examples/` и `src/sionna/model/`.

**Upstream репозитории:**
- ms-van3t: https://github.com/ms-van3t-devs/ms-van3t
- VaN3Twin / NEWWAY: https://github.com/DriveX-devs/NEWWAY

---

## Воспроизводимость

Все evidence-прогоны для ВКР хранятся в `runs/` с разбивкой по датам:

```
runs/
├── 2026-03-04/   # Ключевые прогоны truck_lane_change
├── 2026-03-20/   # Intersection crash прогоны
├── 2026-02-*/    # Ранние sweep-прогоны
└── chatgpt_exports/  # Компактные бандлы для анализа
```

Для воспроизведения конкретного прогона см. `REPORT.md` в директории прогона.

---

## Python-окружения

| Окружение | Назначение |
|---|---|
| `.venv/` | Основные скрипты (`tools/`, `experiments/*/tools/`) |
| `.venv_sionna/` | Sionna-зависимые скрипты |
| `.venv_docs/` | Генерация документации |

Создание:
```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

---

## Цитирование

Если вы используете этот репозиторий в своей работе, пожалуйста, процитируйте базовый фреймворк ms-van3t:

```bibtex
@article{ms-van3t-journal-2024,
    title   = {ms-van3t: An integrated multi-stack framework for virtual validation of V2X communication and services},
    journal = {Computer Communications},
    volume  = {217},
    pages   = {70-86},
    year    = {2024},
    doi     = {https://doi.org/10.1016/j.comcom.2024.01.022},
    author  = {F. Raviglione and C.M. Risma Carletti and M. Malinverno and C. Casetti and C.F. Chiasserini},
}
```

Основная публикация NEWWAY/VaN3Twin (pre-print, 2025):

```bibtex
@misc{pegurri2025newway,
    title         = {Van3tTwin the Multi-Technology V2X Digital Twin with Ray-Tracing in the Loop},
    author        = {Roberto Pegurri and Diego Gasco and Francesco Linsalata and Marco Rapelli and Eugenio Moro and Francesco Raviglione and Claudio Casetti},
    year          = {2025},
    eprint        = {2505.14184},
    archivePrefix = {arXiv},
    url           = {https://arxiv.org/abs/2505.14184},
}
```
