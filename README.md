# SolutionsHub

SolutionsHub is the proposed intake, review, approval, and publishing-handoff system for new Amentum
Solutions / Offerings. It replaces the current Microsoft Form + manual follow-up with a single low-cost
Azure-hosted web application that works for users on two unconnected corporate domains.

This repository currently holds the **scoping documentation only**. No application code or infrastructure
templates have been written yet; those are Phase 1 deliverables described in the roadmap.

## Documents

| Document | What it covers |
|---|---|
| [01 – Solution Scope](docs/01-solution-scope.md) | Executive summary, constraints, target architecture, Azure service selection, cost estimate, requirements traceability, gaps, roadmap, open questions |
| [02 – Workflow & RBAC](docs/02-workflow-and-rbac.md) | Roles, permission matrix, workflow state machine, transitions, notifications, reminders, 6-month review, audit record |
| [03 – Data Model](docs/03-data-model.md) | Intake form field inventory, entities and relationships, capability taxonomy seed data, attachment storage, retention |
| [04 – Authentication & Access](docs/04-auth-and-access.md) | VPN IP allowlisting, magic-link email sign-in, sessions, rate limiting, role administration, threat notes |

## Headline design

- **Hosting:** Azure App Service (B1 Linux) running a Python (FastAPI) web app as a container
- **Data:** Azure SQL Database (Basic) + Azure Blob Storage for attachments
- **Email:** Azure Communication Services Email for sign-in links and workflow notifications
- **Access:** App Service IP access restrictions (VPN egress IPs) at the edge, passwordless magic-link
  sign-in for everyone, application-level RBAC for Reviewer / Approver / Publisher / Admin
- **Cost:** roughly **$19 USD per month** for a single production environment

## Source inputs

The design was derived from two internal documents (not committed to this repository):

- *Solution / Offering Executive Summary – Business Offering Intake* (Microsoft Form, 17 fields)
- *New Solution / Offering Input Process – High-Level Requirements, Through Publishing* (v3)

Their content is captured in the field inventory (doc 03) and requirements traceability matrix (doc 01).
