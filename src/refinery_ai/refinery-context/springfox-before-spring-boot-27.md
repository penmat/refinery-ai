---
type: Migration Planning Rule
title: Convert Springfox before upgrading to Spring Boot 2.7
description: Prevent the Boot upgrade composite from leaving uncompilable Springfox configuration remnants.
tags: [refinery, migration, planning, spring-boot, springfox, springdoc]
status: stable
---

# Convert Springfox before upgrading to Spring Boot 2.7

When a project uses Springfox and both
`org.openrewrite.java.springdoc.SpringFoxToSpringDoc` and
`org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7` are applicable, plan and verify the
Springfox-to-Springdoc conversion first. Make the Boot 2.7 upgrade depend on the documentation
conversion.

The Boot 2.7 composite may remove Springfox dependencies and convert only part of a custom Swagger
configuration. Running the focused documentation conversion first prevents obsolete Springfox
imports and annotations from being left without their supporting dependencies.
