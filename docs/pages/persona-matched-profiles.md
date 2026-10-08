---
title: Persona matched profiles page
page: "/personas/:archetypeId/matched-profiles"
---

# Persona matched profiles page

This page shows one persona archetype in detail and every master profile currently matched to it. Open it by clicking a persona in the Personas list.

## What you can do here
- **Go back**: Back to personas returns to the Personas list.
- **Edit** (admins only): opens Edit Persona Archetype with Persona Name, Category, Summary, LLM Provider, LLM Model and an Active checkbox; Cancel or Save Changes.
- **Read the metadata**: Domain, Category, Matched Profiles, Updated, LLM Provider, LLM Model, the Active/Inactive badge, the persona code and the summary.
- **Read the centroid scores**: Behavior, Engagement, Financial, Loyalty, Relationship and Risk.
- **Open a customer**: click a row in the Master Profiles table to open the customer profile page.
- **Page through profiles**: Prev and Next.

## Sections of the page
- **Archetype card**: name, status, code, summary, the metadata fields and the Centroid Component Scores.
- **Master Profiles table**: all customers matched to this archetype, with a count and Prev/Next pagination. If none match it says "No master profiles matched to this persona archetype."

## Common questions
- **What are the centroid component scores?** The target profile the matching engine compares new profiles against; they are computed, not edited here.
- **How do I change a persona?** Edit (admins only) changes the name, category, summary, model and active flag.
- **Why is Matched Profiles 0?** No profile has been matched to this archetype yet.
- **How do I see a customer's details?** Click the row in the Master Profiles table.
- **What does Active mean?** The archetype is eligible for new profile matching.
