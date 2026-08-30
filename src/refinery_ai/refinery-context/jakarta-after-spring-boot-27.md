---
type: Migration Planning Rule
title: Upgrade to Spring Boot 3 before migrating javax APIs to Jakarta
description: Prevent Jakarta namespace changes from running against an incompatible Spring Boot dependency stack.
tags: [refinery, migration, planning, spring-boot, jakarta, javax]
status: stable
---

# Upgrade to Spring Boot 3 before migrating javax APIs to Jakarta

The `org.openrewrite.java.migrate.jakarta.JavaxMigrationToJakarta` recipe depends on
Spring Boot 3 or greater. Spring Boot 2.7 is still based on the `javax` persistence, servlet, transaction,
and validation APIs. Plan and verify the Spring Boot 2.7 bridge first when needed, then plan and
verify the Spring Boot 3 upgrade before applying any remaining standalone Jakarta migration.

The Spring Boot 3 recipe must not depend on the standalone Jakarta recipe. Apply the Jakarta
recipe only after Spring Boot 3 so Spring Framework, Hibernate, Servlet, Validation, and
application imports move to compatible Jakarta generations. If the Spring Boot 3 upgrade already
removes all applicable `javax` usages, do not schedule the standalone Jakarta recipe.
