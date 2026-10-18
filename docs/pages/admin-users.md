---
title: System users page
page: "/admin/users"
---

# System users page

This page lists the staff accounts that can sign in to this workspace and the active tenant roles assigned to them. It is opened from the Admin tab in the top navigation. Each row is one staff user; the Add/Edit modal manages profile data and role assignments.

## What you can do here
- **Search**: "Search username, email, name…" filters the list as you type. Assigned role labels are also included in the search.
- **Filter by status**: All status, Active, Inactive.
- **Add System User**: opens the create form, including the role selector.
- **Read the table**: User (full name and username), Role, Email, Job Title, Department, Status and Last Login.
- **Edit a user**: use the Edit action; rows do not open a detail page. Root users can manage every role, including `root`.

### The add/edit form
- **Fields**: Username, Full Name, Email, Phone, Job Title, Department, Password, Confirm Password, Status and Assigned roles.
- **Password**: required when creating a user (at least 8 characters, entered twice); it is not shown when editing.
- **Status options**: Active, Inactive.
- **Assigned roles**: a multi-select control that replaces the user's active tenant RBAC assignments. The API validates every selection and only a root user can assign the `root` role.
- **Save**: Save User for a new user or Save Changes when editing; Cancel closes. Deactivate is available when editing. The Username cannot be changed after creation.

## Available roles

The selector contains these ten canonical roles:

| Role | Purpose |
|---|---|
| `root` | Root / Super Admin; unrestricted platform administration and tenant RBAC control |
| `admin` | Application administration, users, security policies, billing and global settings |
| `operator` | Daily operations, monitoring, backups and service maintenance |
| `developer` | Engineering access to development environments, APIs and deployments |
| `auditor` | Read-only logs, histories and security evidence |
| `partner` | Restricted access to shared partner data and dashboards |
| `billing` | Billing / Accountant access to subscriptions, invoices and payments |
| `editor` | Editor / Manager access to create, edit, publish and manage business content |
| `contributor` | Contributor / Author access to create and edit owned work |
| `viewer` | Read-only dashboards, reports and data access |

## Permission and session rules

- System User Management requires a root or tenant-admin role.
- `root` is the super-admin role and is the only role allowed to assign `root` to another user.
- A root user cannot remove their own final `root` role from the UI or API.
- Role changes affect newly issued development tokens. Sign out and sign in again after changing your own roles so the frontend and API receive the updated role claims.
- Tenant isolation is enforced by the API; roles from another tenant are never returned or assignable.

## Common questions
- **How do I add a staff user?** Add System User, set a username and password, then save.
- **How do I give someone a role?** Open Add System User or Edit, select one or more roles, then save. Root is required to assign the root role.
- **How do I remove someone's access?** Open the user with Edit and press Deactivate, or remove their assigned roles.
- **Why can't I change a username?** It is fixed once the user is created.
- **Where do I see a user's role?** Use the Role column in the table or open Edit and read Assigned roles.
- **Why does a user show No role assigned?** The user has no active role assignment visible in the current tenant.
- **Where do I see when someone last signed in?** The Last Login column.
- **Can I reset a password here?** No; the password is only set when creating a user.
