# Permission matrix

**Generated from `apps/api/modules/identity/policy.py` on 2026-10-06.** The code
is the authority; regenerate this table when the policy changes.

Default deny: a capability not listed for a role is refused, and an unknown
capability name is refused for everyone. "MFA" means an owner or administrator
holds the capability only once a second factor is enrolled. Record-level rules
apply on top: a sales representative sees their own leads and deals, delivery
staff see the won deals behind the work they deliver, and an employee sees the
accountability items that concern them.

| Capability | Owner | Admin | Sales mgr | Sales rep | Delivery mgr | Delivery emp |
|---|---|---|---|---|---|---|
| activity.record | yes | yes | yes | yes | yes | yes |
| ai.use | yes | yes | yes | yes | yes | yes |
| contact.manage | yes | — | yes | yes | — | — |
| contact.merge | yes | — | yes | — | — | — |
| contact.view | yes | yes | yes | yes | yes | yes |
| exception.review | yes | — | yes | — | yes | — |
| handover.accept | yes | — | — | — | yes | — |
| import.run | yes | yes | yes | — | — | — |
| invitation.manage | yes (MFA) | yes (MFA) | — | — | — | — |
| lead.manage | yes | — | yes | yes | — | — |
| lead.reassign | yes | — | yes | — | — | — |
| lead.view | yes | yes | yes | yes | yes | yes |
| membership.manage | yes (MFA) | yes (MFA) | — | — | — | — |
| milestone.accept | yes | — | — | — | yes | — |
| milestone.submit | yes | yes | yes | yes | yes | yes |
| opportunity.convert | yes | — | yes | — | — | — |
| opportunity.manage | yes | — | yes | yes | — | — |
| opportunity.transition | yes | — | yes | yes | — | — |
| opportunity.view | yes | yes | yes | yes | yes | yes |
| project.manage | yes | — | — | — | yes | — |
| project.view | yes | yes | yes | yes | yes | yes |
| receipt.record | yes (MFA) | — | — | — | — | — |
| report.company | yes | — | — | — | — | — |
| report.team | yes | — | yes | — | yes | — |
| rule.manage | yes (MFA) | yes (MFA) | — | — | — | — |
| task.assign_others | yes | — | yes | — | yes | — |
| task.manage | yes | yes | yes | yes | yes | yes |
| task.view | yes | yes | yes | yes | yes | yes |
| ticket.manage | yes | yes | yes | yes | yes | yes |
| ticket.view | yes | yes | yes | yes | yes | yes |
| workspace.configure | yes (MFA) | yes (MFA) | — | — | — | — |
| workspace.export | yes (MFA) | — | — | — | — | — |

The client role (Release 3) holds no capability in Release 1.
