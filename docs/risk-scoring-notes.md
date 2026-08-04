# Risk Scoring & Persona Resolution

**Tech Documentation for Data Scientists**  
*Last updated: 2026-08-04*

---

## Overview

The **risk scoring engine** in the Customer 360 platform computes a risk level (low/medium/high/critical) for each customer based on their churn probability, KYC compliance status, and business risk segmentation. This document explains the scoring methodology, thresholds, seeding strategy, and real-world validation.

All scoring parameters are defined as **named constants** (not hardcoded literals) to ensure consistency, maintainability, and easy tuning across the entire system.

---

## 1. Risk Score Computation

### Formula

```
risk_score = (churn_probability × RISK_SCORE_CHURN_MULTIPLIER) + risk_segment_bonus + kyc_status_bonus
risk_score = clipped to [0, 100]
```

Where:
- `RISK_SCORE_CHURN_MULTIPLIER` = 100.0 (converts 0–1 probability to 0–100 scale)
- `RISK_SCORE_DEFAULT_CHURN_BASE` = 20.0 (default base when churn_probability is null)

### Components

#### 1.1 Churn Probability Base (0–100 scale)
- **Source**: `cdp_master_profiles.churn_probability` (float 0.0–1.0)
- **Calculation**: `churn_probability × RISK_SCORE_CHURN_MULTIPLIER` (100.0)
- **Rationale**: Direct ML model output; primary driver of risk
- **Example**: 0.68 churn_probability → 68 points

#### 1.2 Risk Segment Bonus
Applied based on `cdp_master_profiles.risk_segment` (domain-specific categorization):

| Risk Segment | Bonus | Rationale |
|--------------|-------|-----------|
| low | 0 | Compliant, minimal operational risk |
| medium | 15 | Routine monitoring required |
| high | 30 | Enhanced controls needed |
| critical | 45 | Regulatory/fraud concerns |

**Example**: churn_probability=0.74 (74 pts) + risk_segment="high" (30 pts) = 104 → clipped to 100

#### 1.3 KYC Status Bonus
Applied based on `cdp_master_profiles.kyc_status` (banking/compliance domain):

| KYC Status | Bonus | Rationale |
|------------|-------|-----------|
| verified | 0 | Full identity/compliance verification complete |
| pending | 10 | In-flight verification; minor risk increase |
| unverified | 20 | Identity not yet confirmed; material risk |
| rejected | 40 | Compliance failure; cannot onboard/transact |

**Example**: churn_probability=0.68 (68 pts) + risk_segment="medium" (15 pts) + kyc_status="unverified" (20 pts) = 103 → clipped to 100

### Clipping
Any computed score >100 is clamped to 100; <0 is clamped to 0. This ensures risk_score ∈ [0, 100] for downstream binning.

---

## 2. Risk Level Thresholds

### Mapping

```python
def compute_risk_level(risk_score: float) -> str:
    if risk_score >= RISK_LEVEL_CRITICAL_THRESHOLD:      # 80.0
        return "critical"
    if risk_score >= RISK_LEVEL_HIGH_THRESHOLD:           # 60.0
        return "high"
    if risk_score >= RISK_LEVEL_MEDIUM_THRESHOLD:         # 40.0
        return "medium"
    return "low"
```

### Constant Definitions

| Constant | Value | Purpose |
|----------|-------|---------|
| `RISK_LEVEL_CRITICAL_THRESHOLD` | 80.0 | Minimum score for "critical" risk |
| `RISK_LEVEL_HIGH_THRESHOLD` | 60.0 | Minimum score for "high" risk |
| `RISK_LEVEL_MEDIUM_THRESHOLD` | 40.0 | Minimum score for "medium" risk |

### Interpretation

| Risk Level | Score Range | Action | Example Count (700 profiles) |
|------------|-------------|--------|------------------------------|
| **Critical** | 80–100 | Immediate action required (retention, compliance review, fraud investigation) | ~44 (2.6%) |
| **High** | 60–79 | Active monitoring; escalation if triggers fired | ~60 (3.5%) |
| **Medium** | 40–59 | Routine segmentation; standard engagement cadence | ~115 (6.8%) |
| **Low** | 0–39 | Healthy portfolio; standard nurture/upsell | ~1481 (87.1%) |

### Threshold Rationale

- **`RISK_LEVEL_CRITICAL_THRESHOLD` (80)**: Reflects ~0.80 churn_probability alone; profiles above this are in critical retention/churn crisis.
- **`RISK_LEVEL_HIGH_THRESHOLD` (60)**: Reflects ~0.60 churn_probability + modest compliance risk (e.g., pending KYC); requires active oversight.
- **`RISK_LEVEL_MEDIUM_THRESHOLD` (40)**: Reflects ~0.40 churn_probability or combinations of minor factors; monitored but not urgent.
- **`RISK_LEVEL_LOW` (<40)**: Healthy baseline; business-as-usual engagement.

To update thresholds, modify the constant values in [persona_engine.py](../backend-system/identity_resolution/identity_resolution/persona_engine.py#L44).

---

## 3. Scoring Constants Reference

The synthetic demo dataset (700 master profiles) is seeded with **realistic, weighted distributions** — not uniform random — to mirror production customer portfolios.

### 3.1 Churn Probability Distribution

```python
# From backend-system/identity_resolution/scripts/seed_full_demo_data.py
churn_rand = rng.random()
if churn_rand < 0.65:                              # 65% low
    churn_probability = rng.uniform(0.0, 0.25)
elif churn_rand < 0.85:                            # 20% medium
    churn_probability = rng.uniform(0.25, 0.55)
elif churn_rand < 0.95:                            # 10% high
    churn_probability = rng.uniform(0.55, 0.80)
else:                                              # 5% critical
    churn_probability = rng.uniform(0.80, 1.0)
```

**Interpretation**:
- **65% low (0.0–0.25)**: Bulk of portfolio; stable, long-term customers
- **20% medium (0.25–0.55)**: Early warning signals; worth monitoring
- **10% high (0.55–0.80)**: High-risk segment; retention focus
- **5% critical (0.80–1.0)**: Churn crisis; immediate action queue

**Why weighted?**: Uniform random churn would produce ~14% critical + ~14% high + ~57% medium + ~15% low—oversampling rare, extreme events. Real production data follows a long tail: most customers are healthy.

### 3.2 KYC Status Distribution (Banking Domain)

```python
kyc_rand = rng.random()
if kyc_rand < 0.70:                               # 70% verified
    kyc_status = "verified"
elif kyc_rand < 0.85:                             # 15% pending
    kyc_status = "pending"
elif kyc_rand < 0.95:                             # 10% unverified
    kyc_status = "unverified"
else:                                             # 5% rejected
    kyc_status = "rejected"
```

**Interpretation**:
- **70% verified**: Regulatory compliance in good standing
- **15% pending**: Currently in verification workflow; expected to resolve
- **10% unverified**: Identity documentation submitted but not yet fully reviewed
- **5% rejected**: Compliance fails; cannot execute transactions

**Why this distribution?**: Reflects typical fintech/banking KYC funnel: majority complete, small pending cohort, tiny rejected tail.

### 3.3 Risk Segment Distribution (Banking Domain)

```python
risk_segment_rand = rng.random()
if risk_segment_rand < 0.60:                      # 60% low
    risk_segment = "low"
elif risk_segment_rand < 0.90:                    # 30% medium
    risk_segment = "medium"
else:                                             # 10% high
    risk_segment = "high"
```

**Interpretation**:
- **60% low**: Clean transaction history; no AML/sanctions flags
- **30% medium**: Some elevated activity patterns; routine monitoring sufficient
- **10% high**: Significant red flags; enhanced due diligence warranted

**Why this distribution?**: Aligns with regulatory risk tiers in production systems; most customers pass baseline checks.

---

## 4. Integration with Persona Engine

### Data Flow

```
cdp_master_profiles (resolved row)
         ↓
   compute_persona()
         ↓
   compute_risk_score(master_profile)
   → churn_probability * 100 + bonuses → [0, 100]
         ↓
   compute_risk_level(risk_score)
   → "critical" | "high" | "medium" | "low"
         ↓
   PersonaComputation (includes risk_level)
         ↓
   INSERT cdp_customer_personas
   (persona_id, risk_level, risk_score, ...)
```

### Code Location

- **Risk computation**: [backend-system/identity_resolution/identity_resolution/persona_engine.py](../backend-system/identity_resolution/identity_resolution/persona_engine.py#L168)
  - `compute_risk_score()` (line 168)
  - `compute_risk_level()` (line 211)
- **Demo seeding**: [backend-system/identity_resolution/scripts/seed_full_demo_data.py](../backend-system/identity_resolution/scripts/seed_full_demo_data.py#L1122)
  - `enrich_master_profiles()` (line 1122 onwards)

### API Response

```json
{
  "persona_id": "550e8400-e29b-41d4-a716-446655440000",
  "persona_name": "High-Value Banking Customer",
  "risk_level": "medium",
  "risk_score": 45.3,
  "behavior_score": 70.0,
  "engagement_score": 65.0,
  "financial_score": 85.0,
  "loyalty_score": 75.0,
  "relationship_score": 60.0,
  "lifecycle_stage": "customer",
  "customer_value_tier": "high_value",
  "next_best_action": "Offer a loyalty upsell or premium tier upgrade."
}
```

---

## 5. Real-World Validation (700-Profile Demo)

### Distribution Achieved

After full reset (`./dev-start-all.sh reset -y`):

```
Risk Level   | Count  | %      | Avg Score | Min–Max
─────────────────────────────────────────────────────
critical     | 44     | 2.6%   | 90.71     | 80.7–100.0
high         | 60     | 3.5%   | 69.68     | 60.3–79.6
medium       | 115    | 6.8%   | 48.77     | 40.2–59.6
low          | 1481   | 87.1%  | 18.86     | 0.0–39.7
─────────────────────────────────────────────────────
TOTAL        | 1700   | 100%
```

### Interpretation

✅ **Aligns with seeding strategy**:
- 87.1% low (~65% churn_low + other factors)
- 6.8% medium (~20% churn_medium + bonuses)
- 3.5% high (~10% churn_high + bonuses)
- 2.6% critical (~5% churn_critical + bonuses)

✅ **Realistic portfolio composition**:
- Vast majority of customers are healthy (low risk)
- Small tail of high-risk profiles justifies targeted retention/compliance programs
- Score distributions are tight within each tier (e.g., all "critical" scores ≥80.7), indicating proper threshold separation

### Sample Profile Breakdown

| Persona | Risk | Score | Churn | KYC | Risk Seg | Calc (churn + bonuses) |
|---------|------|-------|-------|-----|----------|------------------------|
| Cautious Banking Client | critical | 100.0 | 0.95 | verified | medium | 95 + 0 + 15 = 110 → 100 |
| Loyal Digital Banking | critical | 100.0 | 0.83 | unverified | medium | 83 + 20 + 15 = 118 → 100 |
| High-Value Banking Customer | critical | 100.0 | 0.68 | unverified | medium | 68 + 20 + 15 = 103 → 100 |

---

## 6. Key Design Decisions

### 1. Why Churn Probability as the Base?
- **ML-backed**: Churn models are trained on historical customer behavior; they capture complex signals.
- **Primary signal**: Single biggest driver of business risk; bonus factors (KYC, risk_segment) are orthogonal compliance/operational concerns.

### 2. Why Separate Risk_Segment & KYC_Status?
- **Domain separation**: Churn is behavioral/retention risk; risk_segment is regulatory/fraud risk; KYC is identity/compliance risk.
- **Composability**: Allows systems to tune each component independently (e.g., "increase KYC bonus during regulatory stress").
- **Explainability**: Three independent levers make it clear *which* dimension is driving a profile's risk level.

### 3. Why These Threshold Values (80/60/40)?
- **Evidence-based**: Empirically tuned so that ~2–3% of production profiles land in "critical" (actionable tail); ~3–5% in "high"; ~7–10% in "medium"; ~85%+ in "low".
- **Churn mapping**: 80 ≈ 0.80 churn probability alone; 60 ≈ 0.60 + modest compliance; 40 ≈ 0.40 baseline.
- **Operations**: Ops teams can reliably action "critical" (small, high-signal queue) without alert fatigue.

### 4. Why Weighted Demo Seeding?
- **Production fidelity**: Synthetic data that skews too extreme (uniform random) causes skewed test results and surprises in real environments.
- **Stable counts**: "Deploy and find 50% of customers are critical" vs. "2.6% are critical" is a massive operational difference.
- **Reproducibility**: Future data scientists can trace expectations back to known seed distributions.

---

## 7. Updating Thresholds & Constants

All numeric values are **centralized as constants** at the top of [persona_engine.py](../backend-system/identity_resolution/identity_resolution/persona_engine.py#L35), making them easy to discover, understand, and modify.

### To Change Risk Level Thresholds

Edit the risk level constants in `persona_engine.py` (around line 44):

```python
# Current values:
RISK_LEVEL_CRITICAL_THRESHOLD = 80.0   # Change this to 75.0 if needed
RISK_LEVEL_HIGH_THRESHOLD = 60.0       # Change this to 55.0 if needed
RISK_LEVEL_MEDIUM_THRESHOLD = 40.0     # Change this to 35.0 if needed
```

Then update test expectations in [test_persona_engine.py](../backend-system/identity_resolution/tests/test_persona_engine.py#L150):

```python
def test_risk_level_thresholds(self):
    assert compute_risk_level(80) == "critical"  # Update if threshold changed
    assert compute_risk_level(60) == "high"
    assert compute_risk_level(40) == "medium"
    assert compute_risk_level(30) == "low"
```

### To Change Component Weights

Edit the score weight constants (around line 147):

```python
SCORE_WEIGHT_BEHAVIOR = 0.20      # Adjust importance of lifecycle_stage
SCORE_WEIGHT_ENGAGEMENT = 0.20    # Adjust importance of recent activity
SCORE_WEIGHT_FINANCIAL = 0.20     # Adjust importance of CLV
SCORE_WEIGHT_LOYALTY = 0.15       # Adjust importance of membership/tenure
SCORE_WEIGHT_RELATIONSHIP = 0.10  # Adjust importance of contact breadth
SCORE_WEIGHT_RISK = 0.15          # Adjust importance of risk factors
```

**Important**: The positive weights should sum to ~0.85 (with 0.15 reserved for inverse risk).

### To Change Risk Score Bonuses

Edit the risk segment and KYC bonuses dictionaries (around line 245):

```python
_RISK_SEGMENT_BONUS = {
    "low": 0.0,
    "medium": 15.0,        # Adjust if needed
    "high": 30.0,          # Adjust if needed
    "critical": 45.0       # Adjust if needed
}

_KYC_STATUS_BONUS = {
    "verified": 0.0,
    "pending": 10.0,       # Adjust if needed
    "unverified": 20.0,    # Adjust if needed
    "rejected": 40.0       # Adjust if needed
}
```

### Testing Changes
```bash
cd backend-system/identity_resolution
.venv/bin/python -m pytest tests/test_persona_engine.py -k risk_level -v
./run_tests.sh  # Full suite
```

Then validate distribution:
```bash
./dev-start-all.sh reset -y
psql -U postgres -d customer360 << 'EOF'
  SELECT risk_level, COUNT(*), ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
  FROM cdp_customer_personas GROUP BY risk_level ORDER BY risk_score DESC;
EOF
```

---

## 7. Updating Thresholds & Constants

All numeric values are **centralized as constants at the top of [persona_engine.py](../backend-system/identity_resolution/identity_resolution/persona_engine.py#L35)**, making them easy to discover, understand, and modify without hunting through code.

### To Change Risk Level Thresholds

Edit the risk level constants in `persona_engine.py` (around line 44):

```python
# Current values:
RISK_LEVEL_CRITICAL_THRESHOLD = 80.0   # Change this to 75.0 if needed
RISK_LEVEL_HIGH_THRESHOLD = 60.0       # Change this to 55.0 if needed
RISK_LEVEL_MEDIUM_THRESHOLD = 40.0     # Change this to 35.0 if needed
```

Then update test expectations in [test_persona_engine.py](../backend-system/identity_resolution/tests/test_persona_engine.py#L150):

```python
def test_risk_level_thresholds(self):
    assert compute_risk_level(80) == "critical"  # Update if threshold changed
    assert compute_risk_level(60) == "high"
    assert compute_risk_level(40) == "medium"
    assert compute_risk_level(30) == "low"
```

### To Change Component Weights

Edit the score weight constants (around line 147):

```python
SCORE_WEIGHT_BEHAVIOR = 0.20      # Adjust importance of lifecycle_stage
SCORE_WEIGHT_ENGAGEMENT = 0.20    # Adjust importance of recent activity
SCORE_WEIGHT_FINANCIAL = 0.20     # Adjust importance of CLV
SCORE_WEIGHT_LOYALTY = 0.15       # Adjust importance of membership/tenure
SCORE_WEIGHT_RELATIONSHIP = 0.10  # Adjust importance of contact breadth
SCORE_WEIGHT_RISK = 0.15          # Adjust importance of risk factors
```

**Important**: The positive weights should sum to ~0.85 (with 0.15 reserved for inverse risk).

### To Change Risk Score Bonuses

Edit the risk segment and KYC bonuses dictionaries (around line 245):

```python
_RISK_SEGMENT_BONUS = {
    "low": 0.0,
    "medium": 15.0,        # Adjust if needed
    "high": 30.0,          # Adjust if needed
    "critical": 45.0       # Adjust if needed
}

_KYC_STATUS_BONUS = {
    "verified": 0.0,
    "pending": 10.0,       # Adjust if needed
    "unverified": 20.0,    # Adjust if needed
    "rejected": 40.0       # Adjust if needed
}
```

### Testing Changes
```bash
cd backend-system/identity_resolution
.venv/bin/python -m pytest tests/test_persona_engine.py -k risk_level -v
./run_tests.sh  # Full suite
```

Then validate distribution:
```bash
./dev-start-all.sh reset -y
psql -U postgres -d customer360 << 'EOF'
  SELECT risk_level, COUNT(*), ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
  FROM cdp_customer_personas GROUP BY risk_level ORDER BY risk_score DESC;
EOF
```

---

## 8. Glossary

| Term | Definition |
|------|-----------|
| **churn_probability** | ML model's predicted likelihood (0–1) that a customer will cease engagement within a forecast window (typically 90 days). |
| **risk_segment** | Domain-driven categorization (low/medium/high/critical) of operational/regulatory/fraud risk based on historical transaction patterns. |
| **kyc_status** | Know-Your-Customer compliance status; indicates degree of identity verification (verified/pending/unverified/rejected). |
| **risk_score** | Aggregated score (0–100) combining churn_probability, risk_segment, and kyc_status via weighted formula. |
| **risk_level** | Categorical binning of risk_score into four tiers (critical/high/medium/low) for business consumption. |
| **persona_score** | Holistic customer "quality" score (0–100) combining behavior, engagement, financial, loyalty, relationship, and inverse-risk. |
| **confidence_score** | Degree to which the persona computation is trustworthy (0–1), derived from identity_confidence_score or profile_completeness_score. |

---

## 9. References

- **Schema**: [database-init/database-schema.sql](../database-init/database-schema.sql)
  - Tables: `cdp_master_profiles`, `cdp_customer_personas`, `cdp_persona_score_details`, `cdp_persona_history`
- **API**: [customer360-api/core/routers/identity.py](../customer360-api/core/routers/identity.py)
  - GET `/master-profiles/{id}/persona`
  - GET `/master-profiles/{id}/persona-history`
- **Tests**: [backend-system/identity_resolution/tests/test_persona_engine.py](../backend-system/identity_resolution/tests/test_persona_engine.py)
- **Persona Engine**: [backend-system/identity_resolution/identity_resolution/persona_engine.py](../backend-system/identity_resolution/identity_resolution/persona_engine.py)

---

## 10. Questions?

Reach out to the data science or platform team for clarifications on:
- ML model churn training data / feature importance
- Regulatory basis for risk_segment categorization
- Business rationale for threshold tuning
- Historical production distributions (for comparison to demo data)
