import { readFileSync, readdirSync, writeFileSync } from "node:fs";
const lock = JSON.parse(
  readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"),
);
const notices = [];
for (const [path, metadata] of Object.entries(lock.packages)) {
  if (!path || metadata.dev) continue;
  const directory = new URL(`../${path}/`, import.meta.url);
  const licenses = readdirSync(directory).filter((name) =>
    /^licen[cs]e(?:\.|$)/i.test(name),
  );
  notices.push(
    `## ${path.replace("node_modules/", "")} ${metadata.version}\nLicense: ${metadata.license ?? "See package notice"}\n` +
      licenses
        .map((name) => readFileSync(new URL(name, directory), "utf8"))
        .join("\n"),
  );
}
writeFileSync(
  new URL(
    "../../../src/refinery_ai/monitor/static/THIRD_PARTY_LICENSES.txt",
    import.meta.url,
  ),
  notices.join("\n\n---\n\n"),
);
