---
title: Stage old Spring Boot applications through minor compatibility boundaries
tags: [refinery, migration, planning, spring-boot]
---

When a project is older than Spring Boot 2.7 and the target profile requires a later
Spring Boot release, advance only one supported Spring Boot minor boundary per
migration cycle. For Spring Boot 1.x, first use
`org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_0`. From Spring Boot 2.0
through 2.6, use the corresponding next-minor recipe before planning later upgrades.

Verify and rediscover the project after each boundary. This exposes removed APIs at
the release that removed them and gives deterministic or AI repair a small, specific
failure rather than combining several generations of compatibility changes.
