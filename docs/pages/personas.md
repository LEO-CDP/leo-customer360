---
title: Personas page
page: "/personas"
---

# Personas page

> **Vietnamese version:** [`personas-vi.md`](personas-vi.md)

Persona Management lists the shared customer persona archetypes (`cdp_persona_archetypes`) and shows how many master profiles (`matched_profile_count`) are currently matched to each archetype. The Personas tab is hidden from the top navigation by default and is accessed via `#/personas`. Click any row to open that archetype's matched profiles page.

## What you can do here
- **Filter**: filter by **Domain** (All domains or a specific business domain) and **Status** (`All`, `Active`, `Inactive`).
- **Read the KPIs**: Total Personas, Active / Inactive breakdown, and Avg Confidence.
- **Read the charts**: **Personas by Category** and **Personas by Risk Level**.
- **Read the table**: Persona Archetype (`persona_name` and `persona_code`), Domain, Category, Total Matched Profiles (`matched_profile_count`), Status, and Updated At.
- **Open matched profiles**: click a row or the **Total Matched Profiles** link to inspect the customers assigned to that archetype.
- **Add New Persona** (admins only): opens the create modal with Domain, Persona Code (`persona_code`), Persona Name, Category, Summary, LLM Provider, LLM Model, and an Active checkbox; select **Cancel** or **Create Persona**.

## Sections of the page
- **Header and filters**: page title, **Add New Persona** button, and the Domain and Status filter controls.
- **KPI cards**: Total Personas, Active / Inactive counts, and Avg Confidence.
- **Charts**: Personas by Category and Personas by Risk Level.
- **Personas table**: one row per shared archetype, with a **Load more** button in the footer.

## Common questions
- **What is a persona archetype?** A reusable persona definition (`cdp_persona_archetypes`, one per tenant + domain + `persona_code`) with centroid component scores and a `Vector(768)` embedding that customer profiles are matched against by `PersonaResolutionEngine`.
- **How do I see which customers match a persona?** Click the archetype row or its **Total Matched Profiles** link.
- **How do I create a new persona archetype?** Select **Add New Persona** (admins only), fill in the domain, code, name, and category, then select **Create Persona**.
- **Why is Avg Confidence 0%?** No master profiles have been matched to these archetypes yet.
- **What does Active mean?** The archetype (`is_active = TRUE`) is eligible for profile matching during identity and persona resolution runs.

