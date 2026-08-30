---
type: Migration Planning Rule
title: Upgrade Spring Boot before migrating javax APIs to Jakarta
description: Prevent Jakarta namespace changes from running against an incompatible Spring Boot dependency stack.
tags: [refinery, migration, planning, spring-boot, jakarta, javax]
status: stable
---

# Upgrade Spring Boot before migrating javax APIs to Jakarta

The `org.openrewrite.java.migrate.jakarta.JavaxMigrationToJakarta` recipe depends on
Spring Boot 2.7 or greater. When a project uses an earlier Spring Boot release, plan and verify
the Spring Boot 2.7 upgrade before planning the Jakarta recipe. Do not schedule the Jakarta
recipe in the same plan unless the Spring Boot upgrade is an earlier explicit dependency.

Spring Boot 2.7 is only the minimum sequencing gate; its framework stack still primarily uses
`javax` APIs. Prefer applying the Jakarta namespace migration as part of, or after, the Spring
Boot 3 upgrade so Spring Framework, Hibernate, Servlet, Validation, and application imports move
to Jakarta together. If the Spring Boot 3 upgrade already removes all applicable `javax` usages,
do not schedule the standalone Jakarta recipe.
