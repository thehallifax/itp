# Infrastructure Telemetry Platform

ITP gives an organisation one operational view of its infrastructure: what is
working, what needs attention and which locations are affected. It collects
facts from supported systems and applies fixed, explainable health rules.

## The basic concepts

A **deployment profile** is one customer or organisation boundary. A **site** is
a physical campus, office or data centre inside that profile. The **estate** is
all enabled sites belonging to that one profile.

- Methodist Ladies' College (MLC) is a single-site profile.
- A school group can use one profile containing a head office and several schools.
- A managed service provider uses separate profiles for separate customers.

Separate profiles have separate credentials, stored data, dashboards and
runtime state. A site selector moves between locations inside one organisation;
it never switches customers.

## What the dashboards show

The Operations Wallboard highlights overall state, service health, stale
collectors and high-impact findings. Infrastructure Overview summarises assets
and availability. Vendor dashboards provide engineering detail.

- **Healthy:** current evidence shows normal operation.
- **Warning:** attention is advisable or evidence is incomplete.
- **Critical:** an important service or asset requires immediate attention.
- **Unknown:** there is not enough current evidence.
- **Not Enabled:** the profile has no collector capability for that service.

ITP does not replace backups, security controls, service desks or human change
approval. It does not invent missing telemetry and does not use probabilistic
or generative artificial intelligence for health decisions.

Continue with [deployment models](deployment-models.md) and the
[operator guide](operator-guide.md).
