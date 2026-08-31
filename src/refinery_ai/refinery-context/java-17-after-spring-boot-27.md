---
type: Migration Planning Rule
title: Upgrade Spring Boot 2.x before Java 8 or 11 to Java 17
description: Sequence the Spring Boot 2.7 bridge before upgrading old Spring Boot applications to Java 17.
tags: [refinery, migration, planning, java, spring-boot]
status: stable
---

# Java 8 or 11 to Java 17 with old Spring Boot

When a project is on Java 8 or Java 11 and any Spring Boot release older than 2.7, do not plan the
Java 17 upgrade first. While the project still targets its existing Java version, upgrade Spring
Boot to 2.7.x as the Java-17-capable intermediate release. Verify the Spring Boot 2.7 upgrade before
planning or running the Java 17 upgrade. Make `org.openrewrite.java.migrate.UpgradeToJava17` depend
on `org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7` when both are present in one plan.

This ordering is required even when the existing build can run on a JDK 17 installation. An older
Java source target can produce bytecode that old Spring Framework and ASM versions can inspect;
changing the source target to Java 17 produces class-file version 61, which older Spring Boot
releases may not support.
