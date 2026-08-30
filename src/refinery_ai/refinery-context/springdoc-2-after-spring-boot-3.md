---
type: Migration Planning Rule
title: Upgrade to Spring Boot 3 before Springdoc 2
description: Keep Springdoc aligned with its compatible Spring Framework generation.
tags: [refinery, migration, planning, spring-boot, springdoc, openapi]
status: stable
---

# Upgrade to Spring Boot 3 before Springdoc 2

The `org.openrewrite.java.springdoc.UpgradeSpringDoc_2` recipe depends on Spring Boot 3 and
Spring Framework 6. Do not apply Springdoc 2 while the project is on Spring Boot 2.7 or earlier;
Springdoc 2 references framework APIs such as `org.springframework.http.HttpStatusCode` that are
not present in Spring Framework 5. Plan and verify the Spring Boot 3 upgrade first, then upgrade
Springdoc. The Spring Boot 3 recipe must not depend on the Springdoc 2 recipe.
