---
type: Migration Planning Rule
title: Add the validation starter after the Spring Boot bridge
description: Prevent validation dependency recipes from becoming ineffective no-op prerequisites.
tags: [refinery, migration, planning, spring-boot, validation]
status: stable
---

# Validation starter ordering for Spring Boot 2

Spring Boot 2.3 stopped including Bean Validation through the web and WebFlux starters. When a
project on Spring Boot 2.2 or earlier uses validation annotations, schedule the applicable Spring
Boot 2.3 through 2.7 upgrade before `AddSpringBootStarterValidation`. If both recipes occur in one
plan, make `AddSpringBootStarterValidation` depend on the Spring Boot upgrade.

Do not treat `AddSpringBootStarterValidation` as a completed prerequisite when it makes no source
or build-file changes. On the older Boot baseline its build can remain green only because validation
is still transitive; that does not prove the explicit starter is present for the later Boot upgrade.
