---
type: Migration Planning Rule
title: Normalize application properties to UTF-8 before Spring Boot upgrades
description: Prevent resource-processing failures caused by legacy properties-file encodings.
tags: [refinery, migration, planning, spring-boot, properties, utf-8]
status: stable
---

# Normalize application properties before upgrading Spring Boot

Before planning a Spring Boot upgrade, inspect every `application*.properties` file beneath a
source or test resources directory. If any such file is not valid UTF-8, plan its lossless
conversion to UTF-8 as a prerequisite and verify the unchanged application still builds. Do not
schedule the Spring Boot upgrade before this prerequisite is complete. Newer Maven resource
plugins may reject legacy encodings that older builds accepted.
