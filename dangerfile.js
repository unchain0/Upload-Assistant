const { danger, fail, message, schedule, warn } = require("danger");

const pr = danger.github.pr;
const created = danger.git.created_files || [];
const modified = danger.git.modified_files || [];
const changed = [...new Set([...created, ...modified])];

const body = (pr.body || "")
  .replace(/@coderabbitai\s+ignore/gi, "")
  .trim();

if (body.length < 40) {
  fail("PR description must explain the change and how it was validated.");
}

if (pr.draft) {
  warn("This PR is still a draft; convert it to ready-for-review before merging.");
}

schedule(async () => {
  const [totalLines, lockfileLines] = await Promise.all([
    danger.git.linesOfCode(),
    danger.git.linesOfCode("{package-lock.json,**/package-lock.json}"),
  ]);
  const reviewableLines = Math.max(0, Number(totalLines || 0) - Number(lockfileLines || 0));
  if (reviewableLines > 1500) {
    warn(`Large PR (${reviewableLines} reviewable changed lines, excluding lockfiles). Consider splitting it if the changes are not tightly related.`);
  }
});

const pythonProductionChanged = changed.some(
  (file) =>
    file === "upload.py" ||
    file === "config-generator.py" ||
    file.startsWith("src/") ||
    file.startsWith("bin/"),
);
const testsChanged = changed.some((file) => file.startsWith("tests/"));
if (pythonProductionChanged && !testsChanged) {
  warn("Python production code changed without a matching change under tests/. Confirm existing coverage is sufficient.");
}

const unsafeEnvFiles = created.filter(
  (file) => /(^|\/)\.env($|\.)/.test(file) && !file.endsWith(".example") && !file.endsWith(".sample"),
);
if (unsafeEnvFiles.length) {
  fail(`Do not commit environment files that may contain secrets: ${unsafeEnvFiles.join(", ")}`);
}

const dependencyFiles = changed.filter((file) =>
  ["requirements.txt", "pyproject.toml", "package.json", "package-lock.json", "web_ui/static/js/package.json", "web_ui/static/js/package-lock.json"].includes(file),
);
if (dependencyFiles.length) {
  message(`Dependency/tooling files changed: ${dependencyFiles.join(", ")}. Verify lockfiles and supply-chain impact.`);
}

const workflowFiles = changed.filter((file) => file.startsWith(".github/workflows/"));
if (workflowFiles.length) {
  message(`GitHub Actions changed: ${workflowFiles.join(", ")}. actionlint and the deterministic review workflow will validate them.`);
}

if (changed.some((file) => /(^|\/)Dockerfile(?:\.|$)/.test(file))) {
  message("Docker build instructions changed. Hadolint and the existing multi-architecture Docker build checks will run.");
}
