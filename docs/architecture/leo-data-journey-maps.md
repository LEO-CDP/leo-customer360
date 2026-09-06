# LEO Customer 360 — Data Journey Map

### Business question

> **“Who is this customer, what are they doing now, what are they likely to do next, and what should LEO do about it?”**

| Stage                   | 1. Capture                                 | 2. Assemble                 | 3. Score                          | 4. Segment                           | 5. Syndicate                      | 6. Engage                        |
| ----------------------- | ------------------------------------------ | --------------------------- | --------------------------------- | ------------------------------------ | --------------------------------- | -------------------------------- |
| **Purpose**             | Collect behavioral & transactional signals | Build unified Customer 360  | Turn signals into intelligence    | Decide who belongs to which audience | Push audience/profile to channels | Deliver personalized experience  |
| **Data**                | Web, App, POS, CRM, Ads, Social            | Identity + Profile + Events | RFM, CLV, Lead, Churn, Propensity | Behavioral / Value / Intent          | Audience + attributes + scores    | Content, Offer, Product, Message |
| **Core LEO capability** | Event Tracking                             | Identity Resolution         | Customer Intelligence             | Segmentation                         | Activation                        | Personalization                  |
| **Output**              | Raw Events                                 | Unified Profile             | Customer Scores                   | Audience                             | Activated Audience                | Customer Interaction             |
| **Feedback**            | New event                                  | Profile update              | Score recalculation               | Segment movement                     | Delivery / response               | Conversion / behavior            |

---

## 1. The visual Data Journey

```text
                         LEO CUSTOMER 360
                              DATA JOURNEY

 ┌──────────┐    ┌──────────┐    ┌──────────┐
 │ CAPTURE  │───▶│ ASSEMBLE │───▶│  SCORE   │
 └──────────┘    └──────────┘    └──────────┘
      │               │               │
      ▼               ▼               ▼
 Web / App        Identity         RFM
 POS              Resolution       CLV
 CRM              Customer 360     Lead Score
 GA4              Customer Graph   Churn Score
 Ads              Event History    Propensity
 Social
      │               │               │
      └───────────────┴───────────────┘
                              │
                              ▼
                       ┌───────────┐
                       │  SEGMENT  │
                       └───────────┘
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
             Behavioral           Predictive
              Segment              Segment
                    │                   │
                    └─────────┬─────────┘
                              ▼
                       ┌───────────┐
                       │ SYNDICATE │
                       └───────────┘
                              │
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
           CRM/           Ads/Media         Website/
         Marketing                         App/API
                              │
                              ▼
                       ┌───────────┐
                       │  ENGAGE   │
                       └───────────┘
                              │
                              ▼
                     Customer Response
                              │
                              ▼
                         New Events
                              │
                              └───────────────▶ CAPTURE
```

This makes the important point:

> **LEO Customer 360 is a closed-loop system, not a database.**

---

# 2. Detailed Data Journey

| Stage         | Steps in the Data Journey            | Data Interaction / Platform             | Data Flow                  |
| ------------- | ------------------------------------ | --------------------------------------- | -------------------------- |
| **Capture**   | Customer visits website              | Website + LEO Tracking SDK + GA4        | `page_view`                |
|               | Customer searches                    | Website / App                           | `search`                   |
|               | Customer views product               | Website / App                           | `item_view`                |
|               | Customer adds product                | Website / App                           | `add_to_cart`              |
|               | Customer purchases                   | POS / Ecommerce / CRM                   | `purchase`                 |
|               | Customer responds to campaign        | Email / SMS / Zalo / Ads                | `campaign_*`               |
|               | Customer interacts with social/media | Meta / TikTok / Zalo / Google           | `ad_*`, `social_*`         |
| **Assemble**  | Identify visitor                     | Identity Resolution                     | `visid → profile_id`       |
|               | Resolve known customer               | Phone / Email / CRM ID                  | `identity_resolved`        |
|               | Merge anonymous + known identity     | Identity Graph                          | `profile_merged`           |
|               | Build Customer 360                   | Profile + Events + Transactions         | `profile_updated`          |
|               | Build Customer Graph                 | Customer ↔ Product ↔ Channel ↔ Campaign | `relationship_created`     |
| **Score**     | Calculate RFM                        | Customer transactions                   | `rfm_score_updated`        |
|               | Calculate CLV                        | Transactions + customer history         | `clv_score_updated`        |
|               | Calculate Lead Score                 | Behavior + profile + intent             | `lead_score_updated`       |
|               | Calculate Churn Score                | Behavior + inactivity                   | `churn_score_updated`      |
|               | Calculate propensity                 | ML / AI                                 | `propensity_score_updated` |
| **Segment**   | Behavioral segmentation              | Event stream                            | `segment_entered`          |
|               | Value segmentation                   | RFM / CLV                               | `segment_entered`          |
|               | Intent segmentation                  | Product behavior                        | `segment_entered`          |
|               | Predictive segmentation              | ML score                                | `segment_entered`          |
| **Syndicate** | Send audience to marketing           | CRM / Marketing Automation              | `audience_synced`          |
|               | Send audience to Ads                 | Google / Meta / TikTok                  | `audience_activated`       |
|               | Send profile to website              | Personalization API                     | `profile_activated`        |
|               | Send profile to sales                | CRM                                     | `lead_activated`           |
| **Engage**    | Personalize content                  | Web / App                               | `content_impression`       |
|               | Recommend product                    | Recommendation API                      | `recommendation_shown`     |
|               | Send campaign                        | Email / SMS / Zalo                      | `message_sent`             |
|               | Customer clicks                      | Channel                                 | `message_clicked`          |
|               | Customer converts                    | Ecommerce / POS                         | `purchase`                 |
|               | Feedback returns to LEO              | All channels                            | New event → Capture        |

---

# 3. LEO Event Catalog

I would organize the Event Catalog into **10 event families**, rather than having one huge flat event list.

### A. Identity & Profile

| Event               | Meaning                                | Primary Source  |
| ------------------- | -------------------------------------- | --------------- |
| `identity_created`  | New identity detected                  | LEO             |
| `identity_resolved` | Anonymous identity matched to customer | LEO CIR         |
| `identity_merged`   | Multiple identities merged             | LEO CIR         |
| `profile_created`   | Customer 360 profile created           | LEO             |
| `profile_updated`   | Customer attributes changed            | CRM / POS / LEO |
| `profile_deleted`   | Profile removed                        | CRM / Privacy   |
| `consent_updated`   | Consent preference changed             | CRM / Website   |

### B. Web & App Behavior

| Event             | Meaning                  |
| ----------------- | ------------------------ |
| `session_started` | Customer starts session  |
| `page_view`       | Page viewed              |
| `screen_view`     | Mobile screen viewed     |
| `search`          | Customer performs search |
| `item_view`       | Product viewed           |
| `category_view`   | Product category viewed  |
| `content_view`    | Content viewed           |
| `video_start`     | Video started            |
| `video_complete`  | Video completed          |
| `click`           | CTA / link clicked       |
| `form_start`      | Form started             |
| `form_submit`     | Form submitted           |
| `login`           | Customer logs in         |
| `logout`          | Customer logs out        |

### C. Ecommerce

| Event              | Meaning                   |
| ------------------ | ------------------------- |
| `product_view`     | Product viewed            |
| `add_to_cart`      | Product added             |
| `remove_from_cart` | Product removed           |
| `cart_view`        | Cart viewed               |
| `checkout_start`   | Checkout started          |
| `checkout_step`    | Checkout step completed   |
| `purchase`         | Transaction completed     |
| `refund`           | Transaction refunded      |
| `cancel`           | Order cancelled           |
| `wishlist_add`     | Product added to wishlist |
| `wishlist_remove`  | Product removed           |

### D. Retail / POS

| Event                    | Meaning               |
| ------------------------ | --------------------- |
| `store_visit`            | Customer visits store |
| `pos_transaction`        | POS transaction       |
| `product_purchased`      | Product purchased     |
| `product_returned`       | Product returned      |
| `coupon_redeemed`        | Coupon redeemed       |
| `loyalty_point_earned`   | Points earned         |
| `loyalty_point_redeemed` | Points redeemed       |

### E. Marketing

| Event                   | Meaning                            |
| ----------------------- | ---------------------------------- |
| `campaign_exposed`      | Customer entered campaign exposure |
| `message_sent`          | Message sent                       |
| `message_delivered`     | Message delivered                  |
| `message_opened`        | Message opened                     |
| `message_clicked`       | Message clicked                    |
| `campaign_converted`    | Campaign conversion                |
| `campaign_unsubscribed` | Customer unsubscribed              |
| `ad_impression`         | Ad impression                      |
| `ad_clicked`            | Ad clicked                         |

### F. Lead & Sales

| Event                       | Meaning                      |
| --------------------------- | ---------------------------- |
| `lead_created`              | New lead                     |
| `lead_updated`              | Lead information changed     |
| `lead_qualified`            | Lead qualified               |
| `lead_assigned`             | Lead assigned to salesperson |
| `sales_contacted`           | Sales interaction            |
| `sales_opportunity_created` | Opportunity created          |
| `sales_opportunity_won`     | Opportunity won              |
| `sales_opportunity_lost`    | Opportunity lost             |

### G. Customer Service

| Event                | Meaning               |
| -------------------- | --------------------- |
| `ticket_created`     | Customer service case |
| `ticket_updated`     | Case updated          |
| `ticket_resolved`    | Case resolved         |
| `chat_started`       | Customer starts chat  |
| `chat_completed`     | Chat completed        |
| `complaint_created`  | Complaint registered  |
| `feedback_submitted` | Feedback submitted    |
| `rating_submitted`   | Customer rating       |

### H. Product & Recommendation

| Event                          | Meaning                       |
| ------------------------------ | ----------------------------- |
| `recommendation_requested`     | Recommendation API called     |
| `recommendation_shown`         | Recommendation displayed      |
| `recommendation_clicked`       | Recommendation clicked        |
| `recommendation_added_to_cart` | Recommended product added     |
| `recommendation_purchased`     | Recommended product purchased |
| `content_recommended`          | Personalized content shown    |

### I. Intelligence Events

These are especially important for **AI-first Customer 360**.

| Event                         | Meaning                        |
| ----------------------------- | ------------------------------ |
| `rfm_score_updated`           | RFM recalculated               |
| `clv_score_updated`           | CLV recalculated               |
| `lead_score_updated`          | Lead score changed             |
| `churn_score_updated`         | Churn probability changed      |
| `propensity_score_updated`    | Purchase propensity changed    |
| `intent_detected`             | Customer intent detected       |
| `persona_updated`             | Persona representation changed |
| `next_best_action_generated`  | NBA generated                  |
| `next_best_product_generated` | NBP generated                  |

### J. Segment & Activation

| Event                  | Meaning                      |
| ---------------------- | ---------------------------- |
| `segment_entered`      | Customer enters segment      |
| `segment_exited`       | Customer leaves segment      |
| `audience_created`     | Audience created             |
| `audience_updated`     | Audience changed             |
| `audience_synced`      | Audience sent to destination |
| `activation_started`   | Activation started           |
| `activation_completed` | Activation completed         |
| `activation_failed`    | Activation failed            |

---

# 4. The most important part: Event → Customer 360 transformation

The reference image shows the **data moving and transforming**. For LEO, I would explicitly show this layer:

```text
RAW EVENT
   │
   ▼
┌─────────────────────┐
│ Event Normalization │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Identity Resolution │
│ visid / phone/email │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Customer 360 Profile│
│ Profile + Events    │
│ Transactions        │
│ Relationships       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Feature Engineering │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────────────┐
│ Customer Intelligence       │
│ RFM | CLV | Lead | Churn    │
│ Intent | Propensity | AI    │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────┐
│ Dynamic Segmentation│
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Activation / CDP    │
│ CRM | Ads | Web | App│
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Customer Interaction │
└──────────┬──────────┘
           │
           └───────────────► NEW EVENT
```

---

# 5. Canonical LEO Event Schema

The Event Catalog should ultimately map to one canonical event contract.

```json
{
  "schema_version": "1.0",
  "event_id": "uuid",
  "tenant_id": "PNJ",
  "datetime": "2026-08-22T13:00:00+07:00",
  "unix_timestamp": 1787378400000,

  "event_name": "add_to_cart",
  "metric": "add-to-cart",

  "visid": "anonymous-or-device-id",
  "profile_id": "customer-123",
  "session_id": "session-456",

  "source": {
    "channel": "website",
    "platform": "ecommerce",
    "integration": "leo_tracking_sdk"
  },

  "page": {
    "url": "https://shop.example.com/product/ring-001",
    "title": "Diamond Ring",
    "referrer": "https://google.com"
  },

  "campaign": {
    "utm_source": "google",
    "utm_medium": "cpc",
    "utm_campaign": "summer_sale",
    "utm_content": "ring_banner"
  },

  "customer": {
    "phone_hash": "...",
    "email_hash": "..."
  },

  "product": {
    "product_id": "RING-001",
    "category": "Jewelry",
    "brand": "Example",
    "price": 25000000,
    "quantity": 1
  },

  "custom_data": {},

  "profile_traits": {}
}
```

The important architectural decision is:

> **`event_name` describes what happened.
> `profile_id` describes who did it.
> `source` describes where it happened.
> `datetime` describes when it happened.
> `custom_data` describes the business context.**

---

# 6. Event Catalog → Customer 360

This is where the Event Catalog becomes useful rather than just documentation.

| Event                           | Customer 360 Impact        | Intelligence           |
| ------------------------------- | -------------------------- | ---------------------- |
| `page_view`                     | Update behavioral history  | Interest               |
| `search`                        | Update intent              | Search intent          |
| `item_view`                     | Add product interest       | Product affinity       |
| `add_to_cart`                   | Increase buying intent     | Conversion probability |
| `checkout_start`                | Strong purchase signal     | Purchase propensity    |
| `purchase`                      | Update transaction history | RFM / CLV              |
| `refund`                        | Update value / behavior    | Risk                   |
| `message_opened`                | Update channel affinity    | Engagement score       |
| `message_clicked`               | Increase campaign intent   | Lead score             |
| `store_visit`                   | Update offline behavior    | Omnichannel affinity   |
| `feedback_submitted`            | Update CX profile          | CX score               |
| `identity_resolved`             | Unify customer identity    | Customer 360           |
| `lead_score_updated`            | Update intelligence        | Lead prioritization    |
| `churn_score_updated`           | Update risk                | Retention              |
| `segment_entered`               | Update audience membership | Activation             |
| `recommendation_clicked`        | Update preference          | Personalization        |
| `purchase` after recommendation | Attribute conversion       | Recommendation ROI     |

---

# 7. The LEO version of the original six-stage picture

I would actually use these labels in your final architecture/slide:

```text
CAPTURE
Collect every meaningful customer signal
        ↓
ASSEMBLE
Resolve identity + build Customer 360
        ↓
UNDERSTAND
Calculate scores + intent + AI signals
        ↓
SEGMENT
Create dynamic audiences
        ↓
ACTIVATE
Syndicate profiles & audiences
        ↓
ENGAGE
Personalize the customer experience
        ↓
LEARN
Capture response and feed intelligence back
        ↺
```

I recommend **UNDERSTAND** instead of simply **SCORE** for LEO, because your architecture is going beyond traditional lead scoring into **RFM + CLV + Lead + Churn + Propensity + Intent + Persona + Next Best Action**.

So the final LEO loop becomes:

> **Capture → Assemble → Understand → Segment → Activate → Engage → Learn**

That is a much stronger framing for **AI-first Customer 360** than the original Lead Scoring & Nurturing diagram.
