---
name: git-atomic-commit
description: Split working-tree changes into atomic, single-purpose git commits - survey what changed, group it into logical units, order them so each commit builds, and write messages that match the repository's existing style. Use this whenever the user asks to commit, especially when the tree holds more than one logical change, and whenever they ask to split, separate, reorder, restructure or clean up commits. Also use it when a commit message would need the word "and" to be accurate, or when only part of a file should be staged - the interactive `git add -p` and `git add -i` silently stage nothing in non-interactive environments, so partial staging needs the techniques here.
---

# Atomic commits

An atomic commit contains exactly one logical change. The payoff is not tidiness for
its own sake - it is three concrete abilities the team loses when commits are mixed:

- **Review**: a reviewer can hold one idea in their head and judge it.
- **Revert**: a bad change can be backed out without taking good changes with it.
- **Bisect**: `git bisect` can name the commit that introduced a bug, which only
  works if each commit is one change *and* each commit builds.

That last clause is the one most often missed. Atomic does not mean "as small as
possible" - it means "one complete change". A commit that splits a function away
from the call site it requires is smaller but not atomic, because it leaves the
tree broken.

## The workflow

### 1. Survey before staging anything

Understand the whole tree before deciding how to divide it:

```bash
git status --porcelain      # every path, including untracked
git diff                    # unstaged changes
git diff --cached           # anything already staged
git log --oneline -15       # where HEAD is, and the local style
```

Resist `git add -A` and `git commit -a` as an opening move. They are how a mixed
tree becomes a mixed commit, and once committed, splitting it is strictly more work
than splitting it was beforehand. Also check for changes that should not be committed
at all - build output, `.env` files, debug prints, a stray editor config.

### 2. Group changes into logical units

The most reliable test is linguistic: **draft the commit subject, and if it needs an
"and" to be accurate, you have more than one commit.** "Fix login redirect and bump
lodash" is two. A reviewer's question is the second test: would they have to
understand change X in order to judge change Y? If not, the changes are independent
and belong apart.

Split apart:

| Keep separate | Why |
|---|---|
| Formatting / whitespace vs. logic | A reformat buries the three real lines in 400 noise lines. This is the single highest-value split. |
| Renames vs. behavior changes | Same reason - a rename touches everything and hides the one line that matters. |
| Dependency bumps vs. code | Each needs its own revert story. |
| Unrelated bug fixes | Independent reverts, independent bisects. |
| Refactoring vs. the feature using it | Lets a reviewer confirm the refactor is behavior-preserving. |

Keep together:

| Keep in one commit | Why |
|---|---|
| A change and its tests | They are one logical unit; tests for unshipped code, or code with no tests, is each half a change. |
| A rename and all its call sites | Splitting leaves the tree broken - the opposite of atomic. |
| A signature change and its callers | Same. |
| A change and the docs/changelog describing it | They describe the same decision and should revert together. |

### 3. Order the commits so every one builds

Sequence matters because each commit should leave the tree working. Put foundations
before the things that use them: add the helper before the code that calls it, do
the refactor before the feature built on it, land the schema before the query. If
two groups are mutually dependent, that is a signal they are actually one commit.

### 4. Stage each group

Use the least invasive technique that works. Escalate only when the simpler one cannot
express the split:

**Level 1 - stage whole files.** This covers the large majority of real splits,
because logical changes usually live in different files:

```bash
git add src/auth.py tests/test_auth.py
git commit -m "..."
```

**Level 2 - set aside whole files** when something in the tree would otherwise be
easy to commit by accident:

```bash
git stash push -- requirements.txt    # park it
# ... commit the rest ...
git stash pop                         # bring it back
```

**Level 3 - split changes inside a single file.** Only needed when one file
genuinely holds two logical changes. The mechanics are fiddly and easy to get
subtly wrong, so read `references/splitting-one-file.md` before attempting it.

> **`git add -p` does not work in non-interactive environments, and it fails
> silently.** It prints the first hunk, reaches EOF on stdin, stages *nothing*, and
> **exits 0**. A success exit code here means nothing happened. The same applies to
> `git add -i`, `git commit -p` and `git checkout -p`. Because the failure looks like
> success, the damage is usually a commit that is empty or missing the intended
> change - so use the techniques in the reference file instead, and verify the index
> with `git diff --cached` before committing.

### 5. Write each message in the repository's own voice

Match the surrounding history rather than importing a convention. A `feat:` prefix
dropped into a log of plain prose subjects is noise, and it makes the history look
like two projects. Read the local style first:

```bash
git log --no-merges --format='%s' -n 30               # subject style
git log --no-merges -n 10 --format='%n---%n%s%n%b'    # whether bodies are used
```

Look for: a `type:` or `type(scope):` prefix (Conventional Commits), a ticket prefix
like `[PROJ-123]`, capitalization, imperative vs. past tense ("Add" vs. "Added"),
typical subject length, and whether bodies are normal or subjects stand alone. Adopt
what you find. Conventional Commits is a good default **only** when the log already
uses it, or the repo has no history yet and the user has no preference.

Whatever the local style, the subject should say what changed and the body - when the
history uses bodies - should say *why*, since the diff already shows the what. If the
repo has a `CONTRIBUTING.md` or a commit-msg hook, those override inference from the log.

**Matching a convention never means inventing the data it carries.** If the local style
embeds a ticket or issue reference - `[PROJ-123]`, `Fixes #456` - and you have not been
given the real identifier, do not guess one, and do not continue the numbering you see
in the log. A plausible-looking `[PROJ-124]` is worse than no prefix at all: it is
permanent, it reads as real traceability, and it probably points at somebody else's
unrelated ticket. Omit the reference, match the rest of the style, and say in your reply
that the ID is missing so the user can supply it - the commit is still unpushed and
cheap to amend. Ask for the number if the user is there to ask.

### 6. Verify before handing back

```bash
git log --oneline -n <count>     # the shape of what you built
git status --porcelain           # what, if anything, is left
```

Confirm each commit contains only its own change (`git show --stat <sha>`), and that
nothing was dropped on the floor. If changes remain uncommitted by design, say so
explicitly - a tree the user believed was clean but is not is the worst outcome here,
because they will discover it later and not know whether it was deliberate.

## Guardrails

- **Shape how to commit, not whether to.** If the user has not asked for a commit,
  this skill does not make it appropriate to create one, and it never implies pushing.
- **Never discard work to make a split easier.** `git checkout -- <file>`,
  `git reset --hard` and `git stash drop` destroy uncommitted work that no commit
  holds yet - it is unrecoverable. If a technique needs one of these, back the content
  up first and confirm the backup exists before running it.
- **Don't rewrite published history** to tidy it. Splitting commits already pushed
  means a force-push that rewrites history others may have pulled; ask first.
- **Don't over-split to look thorough.** Three commits that each break the build are
  worse than one that works. When in doubt, prefer the grouping that keeps each
  commit functional.
- **If the tree turns out to hold one logical change, commit it as one.** Not every
  tree needs splitting, and saying so is a fine outcome.
