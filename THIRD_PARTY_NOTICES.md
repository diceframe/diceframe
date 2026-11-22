# Third-party notices

## Local embedding CPU runtime

Windows x64 and managed Docker linux/amd64 releases bundle [ONNX Runtime](https://pypi.org/project/onnxruntime/), [NumPy](https://pypi.org/project/numpy/), [Hugging Face Tokenizers](https://pypi.org/project/tokenizers/), and their dependencies. Runtime versions are recorded in the platform dependency locks. The packaged wheels retain their license and notice files in the Python runtime.

The Windows package includes the app-local x64 Microsoft Visual C++ Runtime DLLs from the Visual Studio redistributable directory, under the [Microsoft Visual Studio redistribution terms](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files).

## Pydantic AI slim 1.107.7

DiceFrame includes and uses `pydantic-ai-slim==1.107.7`, distributed under the MIT License.

Upstream repository and source tag: [pydantic/pydantic-ai at `v1.107.7`](https://github.com/pydantic/pydantic-ai/tree/v1.107.7/pydantic_ai_slim)

Copyright (c) Pydantic Services Inc. 2024 to present

The MIT License (MIT)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Microsoft Edge WebView2 SDK 1.0.4314.35

The Windows portable build ships `Microsoft.Web.WebView2.Core.dll`, `Microsoft.Web.WebView2.WinForms.dll` and `WebView2Loader.dll` from the `Microsoft.Web.WebView2` NuGet package (version 1.0.4314.35) in `app/launcher/webview2/`; the launcher uses them to show DiceFrame in its own window. They are distributed under the BSD 3-Clause License below, also included as `app/launcher/webview2/LICENSE.txt`.

Package: [Microsoft.Web.WebView2 1.0.4314.35](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4314.35)

```text
Copyright (C) Microsoft Corporation. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

   * Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.
   * Redistributions in binary form must reproduce the above
copyright notice, this list of conditions and the following disclaimer
in the documentation and/or other materials provided with the
distribution.
   * The name of Microsoft Corporation, or the names of its contributors 
may not be used to endorse or promote products derived from this
software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
