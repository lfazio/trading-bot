# Trading Bot

# **ROLE**

You are a senior quantitative software engineer and trading system architect.

You design and implement a **production-grade Python trading system** using the XTB API.

Broker: XTB

The system manages:

* EU stock investing (dividend \+ swing trading)  
* Tactical trading (weeks to months)  
* Turbo leveraged instruments (CFDs)

---

# **OBJECTIVE**

Build a system that:

1. Starts at 1000€ capital  
2. Operates via XTB API (single taxable account)  
3. Optimizes **AFTER TAX returns (France CTO assumed)**  
4. Uses phase-based scaling up to 50k€+  
5. Uses turbos with strict selection \+ risk control  
6. Prioritizes capital survival and compounding

---

# **CORE PRINCIPLE (CRITICAL)**

All decisions MUST optimize:

**net return after fees AND taxes**

NOT:

* gross return  
* theoretical performance

---

# **TAX MODEL (MANDATORY ACROSS SYSTEM)**

Assume France CTO taxation:

* 30% flat tax on:  
  * realized capital gains  
  * dividends

---

## **TAX RULES**

### **Realized profit:**

net\_profit \= gross\_profit \* 0.70

### **Dividend:**

net\_dividend \= dividend \* 0.70

### **BACKTESTING MUST INCLUDE TAXES**

No exception.

---

## **TAX-AWARE DECISION RULE (CRITICAL)**

A trade is ONLY valid if:

expected\_net\_profit \> 5 × total\_fees AFTER TAX

---

# **DEVELOPMENT LIFECYCLE & VERIFICATION PROCESS (DO-178C INSPIRED)**

All software development MUST follow a structured, review-gated lifecycle inspired by DO-178C principles.

The system MUST NOT proceed to implementation without completing and validating each phase.

---

# **PHASE 1 — SRS (Software Requirements Specification)**

## **Goal**

Define WHAT the system must do.

## **Output includes:**

* functional requirements  
* non-functional requirements  
* constraints (tax, risk, broker API, phases)  
* traceability identifiers (REQ\_001, REQ\_002...)

## **RULE:**

No design or code is allowed at this stage.

## **GATE:**

Must be explicitly reviewed and approved before proceeding.

---

# **PHASE 2 — SDS (System Design Specification)**

## **Goal**

Define SYSTEM ARCHITECTURE.

## **Output includes:**

* high-level architecture  
* module decomposition  
* data flows  
* external interfaces (XTB API, market data)  
* phase engine design

## **RULE:**

No low-level code allowed.

## **GATE:**

Must be reviewed against SRS traceability.

---

# **PHASE 3 — SDD (Software Design Description)**

## **Goal**

Define DETAILED DESIGN.

## **Output includes:**

* class design  
* algorithms (scoring, turbo selection, risk engine)  
* data structures  
* pseudo-code level logic

## **RULE:**

Must map 1:1 to SDS components.

## **GATE:**

Traceability matrix required (SRS → SDS → SDD).

---

# **PHASE 4 — TEST PLAN DESIGN**

## **Goal**

Define verification strategy BEFORE coding.

## **Output includes:**

* unit test plan  
* integration test plan  
* backtesting validation plan  
* risk validation tests  
* tax correctness tests

## **RULE:**

No implementation allowed.

## **GATE:**

Each requirement MUST have at least one test case.

---

# **PHASE 5 — IMPLEMENTATION**

## **Goal**

Write production Python code.

## **RULES:**

* must follow SDD strictly  
* no undocumented behavior  
* no feature outside SRS  
* every module must map to a requirement ID

---

# **PHASE 6 — TEST EXECUTION**

## **Goal**

Validate correctness.

Includes:

* unit tests  
* integration tests  
* backtesting validation  
* edge cases (market crash, turbo knockout)

---

# **PHASE 7 — VALIDATION & TRACEABILITY**

## **Required outputs:**

* requirement traceability matrix  
* coverage report (requirements → tests → code)  
* known limitations

---

# **CRITICAL RULES (APPLIES TO ALL PHASES)**

1. No phase skipping allowed  
2. No implementation before approval gates  
3. Every requirement must be traceable to:  
   * design  
   * code  
   * test  
4. Changes must restart lifecycle from affected phase  
5. Backtesting must validate all financial logic BEFORE execution use

---

# **IMPORTANT NOTE**

This is not a formal DO-178C certification system.

It is a **DO-178C-inspired engineering discipline model** used to enforce:

* correctness  
* traceability  
* verifiability  
* deterministic behavior

---

# **SYSTEM ARCHITECTURE**

trading\_system/  
├── config/  
├── data/  
├── models/  
├── screener/  
├── strategies/  
├── risk/  
├── tax/  
├── backtesting/  
├── portfolio/  
├── execution/  
├── phase\_engine/  
├── turbo\_selector/  
├── dashboard/  
└── main.py

---

# **PHASE SYSTEM**

---

## **PHASE 1 — CAPITAL BUILDER (0€ → 3000€)**

* Max positions: 3  
* Max trades/month: 4  
* Allocation:  
  * 90% long-term dividend stocks  
  * 10% tactical  
* Turbos: ❌ DISABLED

Focus:

* survival  
* low turnover  
* fee minimization  
* after-tax compounding awareness

---

## **PHASE 2 — STABILITY (3000€ → 10,000€)**

* Max positions: 6  
* Max trades/month: 8  
* Allocation:  
  * 70% stocks  
  * 30% tactical  
* Turbos:  
  * ⚠️ max 1 position  
  * max 5% exposure

Focus:

* diversification  
* controlled trading  
* tax-aware compounding

---

## **PHASE 3 — SYSTEMATIC (10,000€ → 50,000€)**

* Max positions: 12  
* Max trades/month: 20  
* Allocation:  
  * 60% core  
  * 40% tactical  
* Turbos:  
  * enabled  
  * max 10–15% exposure

Focus:

* multi-strategy system  
* risk-balanced portfolio  
* tax efficiency becomes significant

---

## **PHASE 4 — CAPITAL ACCELERATION (\>50,000€) ⭐ NEW**

* Max positions: 20+  
* Trades/month: 40+  
* Allocation:  
  * 50% core (stocks/dividends)  
  * 30% tactical  
  * 20% structured products (turbos)

### **New capabilities:**

* portfolio optimization (risk parity style)  
* position sizing based on volatility  
* regime detection (trend vs range markets)  
* partial automation allowed

### **Turbo usage:**

* fully active but controlled  
* max 20% exposure  
* hedging allowed (long/short balancing)

### **Focus:**

* maximize **net after-tax CAGR**  
* reduce drawdown via diversification  
* systematic scaling

---

# **DIVIDEND & STOCK SCREENER (EU FOCUSED)**

Criteria:

* yield 3–7%  
* payout ratio \< 70%  
* positive free cash flow  
* debt/equity \< 1.5  
* ≥5 years dividend history

Output:

* scored ranking:  
  * stability  
  * yield quality  
  * valuation

---

# **STRATEGY ENGINE**

## **CORE STRATEGY**

* long-term holding  
* dividend compounding  
* low turnover

## **TACTICAL STRATEGY**

* trend following  
* breakout confirmation  
* pullback entries

---

# **TURBO SYSTEM (STRICT)**

Turbos only used via XTB instruments.

## **HARD RULES:**

* no margin assumptions  
* risk \= invested capital only  
* must define:  
  * underlying  
  * direction  
  * leverage  
  * knockout  
  * spread

---

## **TURBO SELECTION MODEL (MANDATORY)**

### **STEP 1 — FILTER**

Reject if:

* knockout distance \< 5%  
* spread \> 1.5%  
* leverage too high (phase-dependent)  
* liquidity too low  
* volatility extreme

---

### **STEP 2 — SCORING**

score \=  
0.35 \* knockout\_distance\_score \+  
0.25 \* leverage\_efficiency \+  
0.20 \* cost\_score \+  
0.20 \* expected\_move\_capture

---

### **STEP 3 — SELECTION**

* rank candidates  
* choose best  
* if score \< threshold → NO TRADE

---

# **RISK ENGINE**

* max drawdown:  
  * Phase 1–2: 15%  
  * Phase 3–4: 20%  
* position limits:  
  * stocks: 25–35%  
  * turbos: phase-based cap  
* risk per trade:  
  * 1–2% capital max  
* stop-loss mandatory

---

# **BACKTESTING ENGINE (MANDATORY)**

Must simulate:

* fees (XTB-like spreads \+ commissions)  
* slippage  
* turbo knockouts  
* dividend payments  
* TAX (30% CTO France)

---

# **PORTFOLIO SYSTEM**

Track:

* cash  
* positions  
* realized gains  
* dividends  
* AFTER-TAX equity curve

---

# **EXECUTION LAYER**

Implement adapter for:

* XTB API (XAPI)

Must support:

* order execution  
* position management  
* leverage instruments (turbos/CFDs)

---

# **PHASE ENGINE**

* auto-detect phase based on capital  
* enforce constraints  
* adjust risk \+ turbo limits dynamically

---

# **CAPITAL FLOW ENGINE (MANDATORY)**

The system MUST track:

* initial capital  
* external capital injections over time  
* total deployed capital  
* portfolio performance net of inflows

## **RULES**

1. All performance metrics must exclude external injections  
2. Phase transitions must use total injected capital \+ equity  
3. Risk sizing must scale with total available capital  
4. Backtesting must simulate injection timeline explicitly

---

# **DASHBOARD**

Must display:

* current phase  
* portfolio allocation  
* turbo exposure  
* net (after-tax) performance  
* drawdown  
* trade history

---

# **BEHAVIORAL RULES (CRITICAL)**

System MUST:

* optimize AFTER TAX returns only  
* avoid overtrading in early phases  
* prefer stocks over turbos unless strong edge  
* reject marginal trades automatically  
* prioritize survival over return

---

# **IMPLEMENTATION ORDER**

1. models  
2. data layer  
3. tax module  
4. XTB execution adapter  
5. phase engine  
6. screener  
7. strategy engine  
8. turbo selection model  
9. risk engine  
10. backtesting  
11. portfolio system  
12. dashboard

---

# **OUTPUT REQUIREMENTS**

* full runnable Python project  
* no pseudo-code  
* no missing modules  
* working `main.py` demo:  
  * connect (mock or XTB)  
  * run screener  
  * generate trades  
  * apply phase logic  
  * simulate portfolio  
  * show after-tax results

---

# **FINAL PRINCIPLE**

This system is not designed to maximize gross returns.

It is designed to:

maximize **sustainable, after-tax capital growth under real-world constraints**

Survival, compounding, and discipline \> performance spikes

# SAFE META-OPTIMIZATION LOOP

# **SAFE META-OPTIMIZATION LOOP (MANDATORY ARCHITECTURE)**

This system implements a **controlled self-improving trading loop**.

It is NOT autonomous trading.

It is a **bounded strategy research engine**.

---

# **🧩 1\. CORE PRINCIPLE**

All strategy evolution MUST satisfy:

No improvement is valid if it increases risk of ruin or overfitting.

---

# **⚙️ 2\. SYSTEM COMPONENTS**

## **Modules**

strategy\_lab/  
├── generator.py \# Claude proposes strategy variants  
├── backtester.py \# deterministic simulation engine  
├── evaluator.py \# computes metrics \+ risk scores  
├── risk\_guard.py \# hard safety constraints  
├── optimizer.py \# selects candidates  
├── registry.py \# stores versions  
└── loop\_controller.py \# orchestration

---

# **🔁 3\. META-OPTIMIZATION LOOP**

The system runs in controlled cycles:

## **STEP 1 — GENERATE (Claude Code)**

Claude generates N strategy variants:

* parameter changes  
* logic adjustments  
* filters/regime detection improvements

⚠️ Constraint: no structural risk increase allowed

---

## **STEP 2 — BACKTEST (Python only)**

Each strategy is tested deterministically:

Outputs:

* CAGR (net after tax)  
* max drawdown  
* Sharpe ratio  
* trade frequency  
* exposure profile  
* tail risk

---

## **STEP 3 — RISK FILTER (HARD GATE)**

Strategies are REJECTED if ANY:

* drawdown \> allowed threshold (phase-based)  
* excessive turnover  
* unstable performance across regimes  
* leverage/turbo exposure too high  
* sensitivity to small parameter changes (overfitting risk)

---

## **STEP 4 — OVERFITTING TEST (CRITICAL)**

Each candidate MUST pass:

### **Walk-forward validation**

* train period  
* validation period  
* out-of-sample period

### **Stability check:**

performance\_variance \< threshold

If performance collapses out-of-sample → REJECT

---

## **STEP 5 — RISK-ADJUSTED SCORING**

Final score:

score \=  
0.4 \* net\_return\_after\_tax  
\+ 0.3 \* sharpe\_ratio  
\+ 0.2 \* stability\_score  
\+ 0.1 \* drawdown\_penalty

---

## **STEP 6 — SELECTION RULE**

* keep top 1–3 strategies  
* MUST outperform current baseline  
* MUST NOT increase risk profile

---

## **STEP 7 — REGISTRY & VERSION CONTROL**

All accepted strategies are stored:

* versioned  
* reproducible  
* immutable once validated

strategy\_v12 → locked  
strategy\_v13 → experimental

---

## **STEP 8 — DEPLOYMENT GATE**

A strategy is ONLY deployed if:

* passes all backtests  
* passes risk guard  
* passes overfitting tests  
* improves risk-adjusted return

Otherwise:

discard or return to generator

---

# **🧠 4\. RISK GUARD (GLOBAL SAFETY LAYER)**

This is the **non-bypassable firewall**

## **HARD LIMITS**

* max drawdown (phase-based)  
* max leverage exposure (turbos)  
* max correlation to existing portfolio  
* max turnover rate

If violated:

strategy is automatically rejected

---

# **📉 5\. ANTI-OVERFITTING RULES**

The system MUST:

* penalize complexity  
* prefer simple strategies  
* reject unstable parameter sensitivity  
* enforce robustness across market regimes

---

# **🔒 6\. SAFE SELF-IMPROVEMENT RULE**

The system is allowed to improve ONLY if:

(new\_strategy\_risk ≤ baseline\_risk)  
AND  
(new\_strategy\_return / risk) \> baseline

Otherwise:

NO CHANGE

---

# **🧭 7\. REGIME AWARENESS (OPTIONAL BUT RECOMMENDED)**

Strategies must be evaluated in:

* bull markets  
* bear markets  
* sideways markets  
* high volatility regimes

Failure in any regime → rejection or downgrade

---

# **🧠 8\. ROLE OF CLAUDE CODE**

Claude Code is ONLY responsible for:

* generating strategy candidates  
* refactoring logic  
* proposing filters  
* explaining failures

Claude MUST NOT:

* simulate results  
* bypass risk constraints  
* override backtest engine

---

# **🚫 9\. HARD SAFETY PRINCIPLE**

No improvement is allowed if it increases tail risk.

---

# **📊 10\. OUTPUT REQUIREMENTS**

Each cycle outputs:

ImprovementReport(  
    best\_strategy\_id=...,  
    improvement\_delta={  
        "return": ...,  
        "drawdown": ...,  
        "sharpe": ...  
    },  
    risk\_assessment=...,  
    rejected\_candidates=\[...\],  
    reason\_for\_rejection=\[...\]  
)

---

# **🧨 FINAL DESIGN PRINCIPLE**

This system is:

* self-improving  
* but NOT self-trusting  
* constrained by deterministic risk logic  
* protected against overfitting and leverage drift

---

# **END OF SPEC**

# GLOBAL KILL SWITCH SYSTEM

# **GLOBAL KILL SWITCH SYSTEM (HARD OVERRIDE LAYER)**

This system defines a **non-negotiable safety override mechanism** that can immediately stop all trading activity.

It has absolute priority over:

* strategy logic  
* risk engine  
* execution layer  
* meta-optimization system  
* auto-execution rules

---

# **⚠️ CORE PRINCIPLE**

Safety overrides profitability at all times.

If any kill switch condition is triggered:

ALL TRADING ACTIVITY MUST STOP IMMEDIATELY.

---

# **🧠 1\. KILL SWITCH STATES**

The system has 3 states:

## **🟢 ACTIVE**

* normal operation  
* trading allowed based on rules

## **🟡 DEGRADED MODE**

* reduced trading activity only  
* no turbos allowed  
* strict risk reduction applied  
* only highest-confidence trades allowed

## **🔴 KILL SWITCH (STOP ALL TRADING)**

* all order execution disabled  
* all strategies paused  
* only diagnostics allowed

---

# **🚨 2\. KILL SWITCH TRIGGERS**

The system MUST switch to KILL SWITCH if ANY condition is met:

---

## **📉 FINANCIAL SAFETY TRIGGERS**

* portfolio drawdown exceeds hard limit:  
  * Phase 1–2: \>15%  
  * Phase 3–4: \>20%  
* single-day loss exceeds threshold (e.g. \>5%)  
* rapid equity decline detected (\>X% in Y days)

---

## **🧠 STRATEGY INSTABILITY TRIGGERS**

* persistent backtest degradation  
* overfitting detection failure (walk-forward collapse)  
* reward function collapse in RL optimizer  
* unstable strategy variance across regimes

---

## **⚙️ EXECUTION ANOMALY TRIGGERS**

* repeated order rejection by broker API (XTB)  
* abnormal slippage or spread expansion  
* missing or corrupted market data feeds

---

## **🧨 SYSTEM INTEGRITY TRIGGERS**

* risk engine failure or inconsistent outputs  
* missing validation layer responses  
* corrupted strategy registry state  
* unexpected behavior in meta-optimizer loop

---

# **🔒 3\. OVERRIDE PRIORITY ORDER**

Kill switch has highest priority in system:

Kill Switch \> Risk Engine \> Strategy Logic \> Execution Layer

No component may override it.

---

# **🧾 4\. KILL SWITCH ACTIONS**

When triggered, system MUST:

1. Immediately cancel pending orders  
2. Close or freeze new trade execution  
3. Disable auto-execution  
4. Freeze strategy updates  
5. Log full system state snapshot  
6. Alert operator (log \+ notification)

---

# **🧠 5\. DEGRADED MODE BEHAVIOR**

When in DEGRADED mode:

* reduce position sizes by ≥50%  
* disable turbos completely  
* allow only top-tier confidence trades  
* increase validation strictness  
* enforce extra risk buffer

---

# **📊 6\. RECOVERY CONDITIONS**

System can ONLY exit KILL SWITCH if ALL conditions are met:

* drawdown recovered below threshold  
* system integrity restored  
* backtests stable again  
* manual re-activation (recommended requirement)

---

# **🧠 7\. MANUAL OVERRIDE RULE (IMPORTANT)**

Even if recovery conditions are met:

manual confirmation SHOULD be required before resuming trading.

---

# **🧩 8\. SYSTEM IMPLEMENTATION REQUIREMENTS**

Add module:

safety/  
    ├── kill\_switch.py  
    ├── monitor.py  
    ├── anomaly\_detector.py  
    ├── state\_manager.py  
    └── alert\_system.py

---

# **🚫 9\. ABSOLUTE RULE**

No module in the system may:

* bypass kill switch state  
* execute trades during KILL SWITCH  
* modify kill switch conditions at runtime

---

# **🧠 FINAL SAFETY PRINCIPLE**

The system must always prefer stopping incorrectly rather than trading incorrectly.

---

# **END OF SPEC**

# Hybrid Capital System

# **ROLE**

You are designing a **hybrid capital management and trading system** using deterministic Python components and Claude-assisted strategy logic.

Execution broker: XTB

The system is NOT a high-frequency trading engine.

It is a **long-term capital compounding and protection system**.

---

# **🎯 CORE OBJECTIVE**

Balance three goals:

1. **Capital Growth (long-term compounding)**  
2. **Capital Safety (drawdown protection)**  
3. **Milestone-based Scaling (controlled growth phases)**

---

# **⚖️ 1\. SYSTEM ARCHITECTURE**

## **Modules:**

capital\_system/  
├── strategy\_engine/ \# trading logic (Claude-assisted)  
├── risk\_engine/ \# hard safety constraints  
├── execution\_layer/ \# broker interface (XTB API)  
├── portfolio\_manager/ \# allocation & exposure control  
├── milestone\_controller/ \# scaling logic (NEW)  
├── kill\_switch/ \# system safety override  
└── analytics/ \# performance \+ monitoring

---

# **📈 2\. THREE SYSTEM MODES**

## **🟢 MODE 1 — GROWTH MODE**

Activated when:

* portfolio is stable  
* drawdown \< moderate threshold  
* no kill switch events recently

### **Behavior:**

* moderate risk exposure  
* allow tactical trades  
* limited turbo usage (only low-risk setups)  
* focus on compounding

Target:

10–15% annual net return

---

## **🟡 MODE 2 — SAFETY MODE**

Activated when:

* volatility increases  
* drawdown approaching limits  
* strategy instability detected

### **Behavior:**

* reduce position sizes by 30–60%  
* disable turbos  
* restrict trading to high-confidence setups only  
* tighten validation thresholds

Target:

capital preservation first

---

## **🔵 MODE 3 — MILSTONE SCALING MODE**

Activated when:

A predefined milestone is reached:

milestones \= \[2000€, 5000€, 10000€, 20000€, 50000€, 100000€, 200000€\]

### **Trigger condition:**

* equity ≥ next milestone  
* AND system stable for ≥ N trading cycles  
* AND no kill-switch events

### **Behavior:**

* unlock increased allocation capacity  
* allow slight risk expansion (bounded)  
* enable new strategy variants  
* optionally inject new capital if configured

⚠️ BUT:  
Risk limits NEVER increase beyond safe bounds.

---

# **🧠 3\. MILESTONE CONTROLLER (KEY COMPONENT)**

This module controls system evolution.

## **RULES:**

### **1\. No automatic scaling without validation**

A milestone is ONLY valid if:

stable\_returns AND low\_drawdown AND strategy\_consistency

---

### **2\. Scaling is gradual, not immediate**

At milestone:

* increase exposure by max \+10–20%  
* NOT exponential scaling  
* NOT leverage increase explosion

---

### **3\. Reject “fake growth”**

System must detect:

* overfitting gains  
* high volatility profit spikes  
* single-trade anomalies

These DO NOT trigger scaling.

---

# **🛑 4\. RISK ENGINE (HARD CONSTRAINTS)**

Never violated:

* max drawdown limit (phase-based)  
* max exposure per asset  
* max correlation risk  
* turbo exposure cap  
* liquidity constraints

If violated:

system enters KILL SWITCH

---

# **🛑 5\. KILL SWITCH (GLOBAL OVERRIDE)**

If triggered:

* all trading stops immediately  
* all strategies frozen  
* only diagnostics allowed

Kill switch overrides ALL other modes.

---

# **📊 6\. STRATEGY ENGINE (CLAUDE ASSISTED)**

Claude is used ONLY for:

* generating strategy variants  
* improving filters  
* proposing regime logic  
* reducing overfitting risk

Claude MUST NOT:

* execute trades  
* bypass risk rules  
* simulate final results

---

# **📉 7\. PORTFOLIO MANAGEMENT RULES**

* diversification mandatory  
* no single-asset dominance  
* turbo exposure capped  
* correlation monitoring required

---

# **🧠 8\. SYSTEM PRINCIPLES**

### **Principle 1:**

Safety \> Growth

### **Principle 2:**

Stability enables scaling

### **Principle 3:**

Milestones are earned, not assumed

### **Principle 4:**

No strategy survives without regime testing

---

# **📈 9\. EXPECTED BEHAVIOR**

System should produce:

* smooth equity curve (not exponential spikes)  
* controlled drawdowns  
* gradual capital acceleration after milestones  
* stable long-term compounding

---

# **🚫 10\. FORBIDDEN BEHAVIORS**

* no aggressive leverage scaling after milestone  
* no continuous risk increase  
* no overfitting-driven optimization loops  
* no bypassing kill switch  
* no “all-in” trades

---

# **🧠 FINAL DESIGN GOAL**

This system is designed to:

Grow capital sustainably while automatically slowing itself down when risk increases and accelerating only when stability is proven.

---

# **END OF SPEC**

# ETF \+ calable

# **ROLE**

You are extending a hybrid systematic trading system with a controlled structured products module.

Execution broker: XTB

Structured products are treated as **derivative-based payoff instruments**, NOT core investments.

---

# **🟠 1\. NEW PORTFOLIO LAYER: INCOME OVERLAY (STRUCTURED PRODUCTS)**

## **PURPOSE**

Enhance portfolio yield in specific market regimes without destabilizing core strategy.

---

## **ALLOCATION RULE**

Structured products MUST be constrained:

max\_allocation \= 0% – 10% of total portfolio

They are strictly OPTIONAL and never mandatory.

---

# **🧠 2\. STRUCTURED PRODUCT CLASSIFICATION ENGINE**

Every product MUST be decomposed into:

### **✔ Underlying asset**

* index / stock / basket

### **✔ Payoff structure**

* autocallable  
* barrier reverse convertible  
* capital protected note  
* leveraged certificate

### **✔ Risk profile**

* max loss scenario  
* barrier distance  
* issuer risk  
* volatility sensitivity

---

# **⚠️ 3\. PAYOFF TRANSPARENCY RULE (MANDATORY)**

The system MUST convert every structured product into an equivalent risk profile:

* equity equivalent exposure  
* hidden leverage estimate  
* worst-case loss scenario  
* break-even probability estimate

If payoff cannot be clearly decomposed:

❌ REJECT PRODUCT

---

# **📉 4\. RISK EQUIVALENCE MAPPING (CRITICAL)**

Structured products MUST be mapped to equivalent instruments:

| Product type | Equivalent risk |
| ----- | ----- |
| Autocallable | conditional equity \+ short volatility |
| Barrier note | synthetic stock position |
| Capital protected note | bond \+ call option |
| Turbo structured note | leveraged derivative exposure |

---

# **🧠 5\. MARKET REGIME FILTER (MANDATORY)**

Structured products can ONLY be used in:

### **✔ Allowed regimes:**

* low volatility  
* sideways markets  
* stable macro conditions

### **❌ Forbidden regimes:**

* high volatility spikes  
* crisis periods  
* liquidity stress environments

System MUST block deployment otherwise.

---

# **📊 6\. STRATEGIC ROLE**

Structured products are defined as:

“Yield smoothing instruments in non-trending environments”

NOT:

* growth drivers  
* core portfolio assets  
* leverage substitutes

---

# **⚖️ 7\. HARD CONSTRAINTS**

System MUST enforce:

* max 10% total allocation  
* no correlation stacking with turbo layer  
* no overlapping downside exposure with stocks  
* issuer diversification requirement

---

# **🧨 8\. FORBIDDEN BEHAVIOR**

The system MUST reject:

* products with opaque payoff structure  
* products with undefined worst-case loss  
* excessive yield chasing (\> threshold risk-adjusted return)  
* stacking structured products \+ turbos on same underlying

---

# **🧠 9\. RISK ENGINE INTEGRATION**

Structured products MUST pass:

### **✔ Stress testing:**

* crash scenario simulation  
* volatility expansion scenario  
* correlation spike scenario

### **✔ Liquidity check:**

* exit constraints evaluated  
* early redemption risks modeled

---

# **📉 10\. PERFORMANCE EXPECTATION RULE**

System MUST assume:

Structured products do NOT increase expected alpha

They only:

* reshape return distribution  
* increase yield in specific regimes  
* add conditional risk exposure

---

# **🧭 11\. FINAL SYSTEM PRINCIPLE**

Structured products are:

“Controlled yield overlays with asymmetric hidden risk”

NOT:

“safe income boosters”

---

# **END OF SPEC**

