LSP Test Server
---

In order to test Remorph's LSP client, we need a working LSP Server with transpileToDatabricks capability.
The lsp_config.yml and lsp_server.py are just that i.e. a working LSP Server that Remorph can run for testing.

The lsp_config.yml contains values that are read by the LSP client and/or passed to the LSP server, and checked.
It points to the lsp_server.py as the executable to run.

The lsp_server itself logs to stderr and a file named `test-lsp-server.log`, either of which can be examined as necessary to validate test cases.
The transpilation performed by the LSP Server is simply to convert the source code to lowercase.

The code is located under resources because it is not directly executed by our CI. Rather, it is launched by our unit tests.
In the end state, it will be part of the Remorph SDK, as a sample
