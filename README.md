# makefun-gta6-trophy-rarity-forecast-01

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/0xPaasepp/makefun-gta6-trophy-rarity-forecast-01/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/0xPaasepp/makefun-gta6-trophy-rarity-forecast-01/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

This program forecasts how many GTA VI players will have earned each trophy in week one. It reads every Exophase trophy case, collapses the history to one snapshot per page refresh, and models earners and players separately before dividing: players tracked are extrapolated on the log scale against log time (and never forecast below the count already tracked), and each trophy's earn rate is extrapolated on the logit scale with its slope shrunk toward its tier mean. When the history is still flat on launch night, it falls back on refresh-aware priors taken from the brief's own published aggregate totals, so a first refresh dilutes early rates instead of freezing them. The output is five quantiles per trophy, widened by random-walk, refresh, and small-count uncertainty on the logit scale. It is pure Python standard library, deterministic, and prints one diagnostic line per case to standard error with the forecast players tracked and the implied total earners, so the aggregates can be checked against the brief's published totals.

## Run it

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=../kit/gta6-trophy-rarity-forecast-01-kit/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=ddn7bre1&utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
