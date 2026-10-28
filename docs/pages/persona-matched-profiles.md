---
title: Persona matched profiles page
page: "/personas/:archetypeId/matched-profiles"
---

# Persona matched profiles page

> **Vietnamese version:** [`persona-matched-profiles-vi.md`](persona-matched-profiles-vi.md)

This page shows one shared persona archetype (`cdp_persona_archetypes`) in detail and lists every master customer profile (`cdp_master_profiles`) currently assigned to it via active `cdp_customer_personas` rows. Open it by clicking a persona row on the **Personas** page.

## What you can do here
- **Go back**: select **Back to personas** to return to the Personas list.
- **Edit** (admins only): opens **Edit Persona Archetype** with Persona Name, Category, Summary, LLM Provider, LLM Model, and an Active checkbox; select **Cancel** or **Save Changes**. Saving triggers an asynchronous Dagster run to refresh `matched_profile_count`.
- **Read the metadata**: Domain, Category, Matched Profiles (`matched_profile_count`), Updated timestamp, LLM Provider, LLM Model, the Active/Inactive badge, the `persona_code`, and the narrative summary.
- **Read the centroid scores**: the six target component scores (`Behavior`, `Engagement`, `Financial`, `Loyalty`, `Relationship`, and `Risk`) that `PersonaResolutionEngine` compares profiles against.
- **Open a customer**: click any row in the **Master Profiles** table to open that customer's 360° profile page.
- **Page through profiles**: use **Prev** and **Next** pagination controls.

## Sections of the page
- **Archetype card**: persona name, active status badge, `persona_code`, narrative summary, metadata fields, and the six **Centroid Component Scores**.
- **Master Profiles table**: all customers currently matched to this archetype (`is_active = TRUE` on `cdp_customer_personas`), with a total count and Prev/Next pagination. If none match, the table displays `"No master profiles matched to this persona archetype."`

## Common questions
- **What are the centroid component scores?** They represent the ideal target profile vector (`Behavior`, `Engagement`, `Financial`, `Loyalty`, `Relationship`, `Risk`) used by `PersonaResolutionEngine` to score and assign master profiles; they are computed automatically rather than edited manually in this form.
- **How do I change a persona archetype?** Select **Edit** (admins only) to update the display name, category, summary, LLM provider/model, or active status.
- **Why is Matched Profiles 0?** No master profile has been matched to this archetype yet, or the persona resolution job has not run since the archetype was created.
- **How do I inspect a matched customer's details?** Click the customer's row in the **Master Profiles** table to open their profile detail page.
- **What does Active mean?** The archetype (`is_active = TRUE`) is eligible for new customer profile assignments during persona resolution runs.

