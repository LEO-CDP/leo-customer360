---
title: System users page
page: "/admin/users"
---

# System users page

> **Vietnamese version:** [`admin-users-vi.md`](admin-users-vi.md)

This page lists the staff accounts (`sys_user`) that can sign in to this workspace and the active tenant RBAC roles assigned to them. Open it from the **Admin** tab in the top navigation. Each row represents one staff user; the **Add/Edit System User** modal manages profile details and tenant role assignments.

## What you can do here
- **Search**: "Search username, email, name…" filters the list as you type (including role labels, job titles, and departments).
- **Filter by status**: All status, Active, Inactive.
- **Add System User**: opens the create form, including the multi-select role control.
- **Read the table**: User (full name and username), Role, Email, Job Title, Department, Status, and Last Login.
- **Edit a user**: use the **Edit** action on a row; rows do not open a detail page. Root users can manage every role, including `root`.

### The add/edit form
- **Fields**: Username, Full Name, Email, Phone, Job Title, Department, Password, Confirm Password, Status, and Assigned roles.
- **Password**: required when creating a user (at least 8 characters, entered twice); hidden when editing.
- **Status options**: Active (`1`), Inactive (`0`).
- **Assigned roles**: a multi-select control (`PUT /api/v1/users/{user_id}/roles`) that replaces the user's active tenant RBAC assignments. Only a `root` user can assign the `root` role.
- **Save**: **Save User** for a new user or **Save Changes** when editing; **Cancel** closes. **Deactivate** is available when editing. The **Username** cannot be changed after creation.

## Available roles

| Role | Purpose |
|---|---|
| `root` | Root / Super Admin; unrestricted platform administration and tenant RBAC control |
| `admin` | Application administration, users, security policies, billing, and global settings |
| `operator` | Daily operations, monitoring, backups, and service maintenance |
| `developer` | Engineering access to development environments, APIs, and deployments |
| `auditor` | Read-only logs, histories, and security evidence |
| `partner` | Restricted access to shared partner data and dashboards |
| `billing` | Billing / Accountant access to subscriptions, invoices, and payments |
| `editor` | Editor / Manager access to create, edit, publish, and manage business content |
| `contributor` | Contributor / Author access to create and edit owned work |
| `viewer` | Read-only dashboards, reports, and data access |

## Permission and session rules

- System User Management requires a `root` or tenant-admin (`admin`) role.
- `root` is the only role allowed to assign `root` to another user, and a `root` user cannot remove their own final `root` role.
- Role changes affect newly issued tokens. Sign out and sign in again after changing your own roles.
- Tenant isolation is enforced by the API and PostgreSQL RLS.

## Common questions
- **How do I add a staff user?** Select **Add System User**, set a username, password, and roles, then save.
- **How do I give someone a role?** Open **Add System User** or **Edit**, select roles in **Assigned roles**, then save.
- **How do I remove someone's access?** Open the user with **Edit** and select **Deactivate**, or remove their assigned roles.
- **Why can't I change a username or reset a password?** Username is fixed after creation, and the password field is only shown when creating a user.
- **Why does a user show No role assigned?** The user has no active role assignment in the current tenant.


