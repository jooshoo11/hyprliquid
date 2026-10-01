# 🔭 Missed Opportunities & Decision Retrospective Journal

**Session Start**: `2026-10-01T01:10:00Z`  
**Tracking**: Macro regime filters, circuit breakers, orderbook spread guards, and anti-churn cooldowns.

| Timestamp (UTC) | Coin | Strategy | Reason / Decision | Key Metrics | Retrospective Note |
|:---|:---|:---|:---|:---|:---|
| 2026-10-01 01:11:31 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 04:05:00 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 04:07:02 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 04:26:54 | LIT | TrendContinuationSMC | Exhaustion Gate: 5m RSI deeply overbought (68.1 > 68) | rsi_5m=68.09035236357853, bias=LONG, mark_price=4.066 | - |
| 2026-10-01 04:41:55 | LIT | TrendContinuationSMC | Exhaustion Gate: 5m RSI deeply overbought (68.4 > 68) | rsi_5m=68.42554377825695, bias=LONG, mark_price=4.092769 | - |
| 2026-10-01 04:45:26 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 04:56:56 | NEAR | TrendContinuationSMC | Exhaustion Gate: 5m RSI deeply overbought (70.1 > 68) | rsi_5m=70.10839501003888, bias=LONG, mark_price=5.4405 | - |
| 2026-10-01 05:01:54 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 05:07:26 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 05:19:32 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 05:29:00 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 05:35:02 | NEAR | TrendContinuationSMC | Exhaustion Gate: 5m RSI deeply overbought (69.6 > 68) | rsi_5m=69.61024431422327, bias=LONG, mark_price=5.4672 | - |
| 2026-10-01 06:05:27 | ENA | TrendContinuationSMC | Exhaustion Gate: 5m RSI deeply overbought (68.5 > 68) | rsi_5m=68.54880881905785, bias=LONG, mark_price=0.27543 | - |
| 2026-10-01 06:37:28 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 06:48:58 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 06:55:09 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 07:06:52 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 07:29:03 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 07:42:59 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 07:44:39 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 07:56:10 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 08:11:59 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 08:15:34 | STX | VwapOiMomentum | Pre-Entry Gate: Spread 9.1 bps > 8 bps | spread_bps=9.090200763577306, bid_depth=3643.297035, ask_depth=2861.094098 | - |
| 2026-10-01 08:15:34 | NEAR | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($8,187 < $30k) | spread_bps=0.19012491206847373, bid_depth=20798.02723, ask_depth=8187.4273600000015 | - |
| 2026-10-01 08:15:34 | ENA | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($12,607 < $30k) | spread_bps=2.671959691580063, bid_depth=25682.191600000002, ask_depth=12606.69297 | - |
| 2026-10-01 08:15:34 | MON | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($858 < $30k) | spread_bps=1.843997787202367, bid_depth=858.422697, ask_depth=1622.1725099999999 | - |
| 2026-10-01 08:16:32 | STX | VwapOiMomentum | Pre-Entry Gate: Spread 10.3 bps > 8 bps | spread_bps=10.34206375882337, bid_depth=2595.276842, ask_depth=1568.645494 | - |
| 2026-10-01 08:16:33 | NEAR | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($14,747 < $30k) | spread_bps=0.9513299592842404, bid_depth=23711.17049, ask_depth=14747.266440000001 | - |
| 2026-10-01 08:16:33 | ENA | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($4,304 < $30k) | spread_bps=1.1437721605846387, bid_depth=4303.99802, ask_depth=11678.24749 | - |
| 2026-10-01 08:16:33 | MON | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($892 < $30k) | spread_bps=4.292635064696181, bid_depth=891.7634489999999, ask_depth=1565.20282 | - |
| 2026-10-01 08:17:29 | STX | VwapOiMomentum | Pre-Entry Gate: Spread 9.5 bps > 8 bps | spread_bps=9.543215289777963, bid_depth=5271.586134, ask_depth=3407.624567 | - |
| 2026-10-01 08:17:29 | ENA | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($4,721 < $30k) | spread_bps=0.3815483230955016, bid_depth=4721.37212, ask_depth=18574.09691 | - |
| 2026-10-01 08:17:29 | NEAR | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($9,283 < $30k) | spread_bps=1.5220700152205324, bid_depth=9283.248029999999, ask_depth=22021.66438 | - |
| 2026-10-01 08:17:29 | MON | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($531 < $30k) | spread_bps=3.6845983787761374, bid_depth=530.837483, ask_depth=1141.5183789999999 | - |
| 2026-10-01 08:18:25 | STX | VwapOiMomentum | Pre-Entry Gate: Spread 10.0 bps > 8 bps | spread_bps=10.003591032678434, bid_depth=2879.433698, ask_depth=2740.1188939999997 | - |
| 2026-10-01 08:18:25 | NEAR | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($3,570 < $30k) | spread_bps=1.8975332068317534, bid_depth=3570.14386, ask_depth=17530.491 | - |
| 2026-10-01 08:18:25 | ENA | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($8,912 < $30k) | spread_bps=1.9021532374646006, bid_depth=11471.75281, ask_depth=8912.2495 | - |
| 2026-10-01 08:18:25 | AAVE | TrendContinuationSMC | Pre-Entry Gate: Insufficient depth ($5,988 < $30k) | spread_bps=1.2088974854938488, bid_depth=5988.306500000001, ask_depth=8867.0173 | - |
| 2026-10-01 08:18:25 | MON | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($601 < $30k) | spread_bps=0.3069461923327911, bid_depth=600.508018, ask_depth=2127.393911 | - |
| 2026-10-01 08:19:11 | STX | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($2,136 < $30k) | spread_bps=5.152514427039828, bid_depth=2871.251511, ask_depth=2136.0011649999997 | - |
| 2026-10-01 08:19:12 | MON | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($499 < $30k) | spread_bps=2.769742106233496, bid_depth=498.96080500000005, ask_depth=1812.4657129999998 | - |
| 2026-10-01 08:19:12 | LIT | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($547 < $30k) | spread_bps=0.756258035242101, bid_depth=3383.5784, ask_depth=547.488 | - |
| 2026-10-01 08:19:12 | ENA | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($5,367 < $30k) | spread_bps=0.38109756097387537, bid_depth=28030.62712, ask_depth=5367.20153 | - |
| 2026-10-01 08:19:12 | NEAR | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($7,799 < $30k) | spread_bps=1.7128827817227166, bid_depth=7799.08708, ask_depth=24754.90064 | - |
| 2026-10-01 08:20:49 | STX | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($2,958 < $30k) | spread_bps=7.486575795126303, bid_depth=2957.540511, ask_depth=3777.718927 | - |
| 2026-10-01 08:20:49 | ENA | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($9,790 < $30k) | spread_bps=0.3812137846908356, bid_depth=9790.18882, ask_depth=14927.4091 | - |
| 2026-10-01 08:20:49 | NEAR | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($6,890 < $30k) | spread_bps=0.954872715466499, bid_depth=6889.52206, ask_depth=9167.438380000001 | - |
| 2026-10-01 08:20:49 | MON | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($524 < $30k) | spread_bps=1.2338824110053246, bid_depth=523.621926, ask_depth=708.9195550000001 | - |
| 2026-10-01 08:20:50 | AAVE | TrendContinuationSMC | Pre-Entry Gate: Insufficient depth ($13,876 < $30k) | spread_bps=1.2120477546821544, bid_depth=13875.8164, ask_depth=14479.0846 | - |
| 2026-10-01 08:20:50 | LIT | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($127 < $30k) | spread_bps=1.7679446380768424, bid_depth=4465.9773, ask_depth=126.7376 | - |
| 2026-10-01 08:22:01 | STX | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($1,858 < $30k) | spread_bps=4.901707858210824, bid_depth=1857.7033589999999, ask_depth=2408.66852 | - |
| 2026-10-01 08:22:01 | MON | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($1,341 < $30k) | spread_bps=2.7688047992622864, bid_depth=1499.101973, ask_depth=1340.656936 | - |
| 2026-10-01 08:22:01 | LIT | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($369 < $30k) | spread_bps=1.5129737499052727, bid_depth=3517.2947, ask_depth=368.8902 | - |
| 2026-10-01 08:22:47 | STX | VwapOiMomentum | Pre-Entry Gate: Spread 9.0 bps > 8 bps | spread_bps=9.03575577642859, bid_depth=3195.955041, ask_depth=2856.9578659999997 | - |
| 2026-10-01 08:22:47 | MON | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($908 < $30k) | spread_bps=2.1515291224834985, bid_depth=908.0758219999999, ask_depth=4225.628898 | - |
| 2026-10-01 08:22:48 | LIT | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($139 < $30k) | spread_bps=0.2516989680347622, bid_depth=5100.1622, ask_depth=139.0675 | - |
| 2026-10-01 08:22:48 | ENA | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($4,662 < $30k) | spread_bps=0.37982376177491645, bid_depth=6640.407719999999, ask_depth=4662.40524 | - |
| 2026-10-01 08:23:34 | STX | VwapOiMomentum | Pre-Entry Gate: Spread 11.1 bps > 8 bps | spread_bps=11.091049780757956, bid_depth=3209.880096, ask_depth=2873.206639 | - |
| 2026-10-01 08:23:34 | MON | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($2,334 < $30k) | spread_bps=0.3084040092524287, bid_depth=2334.036567, ask_depth=11557.972143 | - |
| 2026-10-01 08:23:34 | LIT | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($214 < $30k) | spread_bps=1.511335012594292, bid_depth=4937.871, ask_depth=214.428 | - |
| 2026-10-01 08:23:34 | ENA | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($4,937 < $30k) | spread_bps=0.37992477489495086, bid_depth=4937.15852, ask_depth=12466.99538 | - |
| 2026-10-01 08:24:20 | STX | VwapOiMomentum | Pre-Entry Gate: Spread 9.3 bps > 8 bps | spread_bps=9.292480834258976, bid_depth=2398.047659, ask_depth=4471.4530509999995 | - |
| 2026-10-01 08:24:20 | MON | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($980 < $30k) | spread_bps=3.7101162503107417, bid_depth=980.443735, ask_depth=2138.854061 | - |
| 2026-10-01 08:24:21 | LIT | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($1,717 < $30k) | spread_bps=3.521923976756031, bid_depth=1717.1427, ask_depth=4628.9936 | - |
| 2026-10-01 08:24:21 | ENA | VwapOiMomentum | Pre-Entry Gate: Insufficient depth ($8,900 < $30k) | spread_bps=0.760687661646889, bid_depth=9877.38079, ask_depth=8899.6159 | - |
| 2026-10-01 08:27:06 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 08:34:18 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 08:38:19 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
| 2026-10-01 08:44:02 | SOL | OrderBookImbalance | L2 imbalance threshold not met | bid_ask_ratio=1.1 | Wait for ratio > 1.5 |
