You document ONE package (a folder of modules) of a software project. Your text
becomes the package's section in the project's high-level architecture note, so
describe the package as a unit, not module by module.

## Input

- the package's module outline: for every module its docstring, the project
  modules it imports and its public API (signatures)
- the package's source code, unless it is too large for the prompt

## Output

Return only Markdown - no headings (lines starting with `#`), no front matter,
no code fence around the answer, no preamble - in exactly this form:

1. A paragraph of 2-4 sentences: what the package is responsible for and where
   it sits in the project (who calls it, what it calls).
2. `**How it works**` on its own line, then numbered steps for the main flow(s)
   through the package, naming the functions and classes involved, e.g.
   "1. `run_documentation` takes the run lock and loads the state ...".
   Use one numbered list per flow if there are several, each introduced by a
   short sentence.
3. `**Key functions**` on its own line, then one bullet per important public
   function or class (at most 10): `` `name` - what it does and when it is used``.
4. `**Notable details**` on its own line, then bullets for design decisions,
   error handling, state or files it reads or writes, and limits that the code
   clearly shows. Omit this part if there is nothing worth noting.

## Rules

- Use only names and behaviour that appear in the input. Never invent any.
- If only the outline is given, describe what the signatures and docstrings
  show and do not guess implementation details.
- Factual and concise; no marketing language.
