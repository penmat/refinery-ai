---
type: Migration Planning Rule
title: Upgrade Spring Boot before Java 11 to Java 17
description: Sequence the Spring Boot 2.7 bridge before upgrading Java 11 applications to Java 17.
tags: [refinery, migration, planning, java, spring-boot]
status: stable
---

# Java 11 to Java 17 with old Spring Boot

When a project is on Java 11 and Spring Boot 2.1.x, do not plan the Java 17 upgrade first. While
still on Java 11, upgrade Spring Boot 2.1.x to a Java-17-capable release—preferably Spring Boot
2.7.x as an intermediate step. Verify that upgrade before planning Java 11 to Java 17.
