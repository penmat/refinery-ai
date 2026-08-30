---
type: Migration Planning Rule
title: Defer data.sql until Hibernate creates JPA tables
description: Preserve data initialization order across Spring Boot 2.5 and later upgrades.
tags: [refinery, migration, planning, spring-boot, jpa, sql]
status: stable
---

# Defer datasource initialization before upgrading Spring Boot

When a JPA application uses Hibernate-generated tables together with `data.sql`, add
`spring.jpa.defer-datasource-initialization=true` before upgrading to Spring Boot 2.5 or later.
Verify this deterministic compatibility step before the Spring Boot upgrade. Without it, Spring
Boot may execute `data.sql` before Hibernate creates the tables and application startup will fail.
