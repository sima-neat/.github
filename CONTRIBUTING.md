# Contributing to SiMa.ai

Thank you for your interest in contributing to **SiMa.ai**!  

Our mission is to make **AI at the Edge** effortless and impactful — combining **Vision, Voice, and Language AI** with **low-power, high-performance system enablement**.  

We welcome contributions from the community in the form of **bug reports, feature requests, code contributions, documentation improvements, and new demos**.

---

## How to Contribute

### 1. Reporting Issues
- Use the **Issue Templates** (bug report, feature request, or documentation) when opening an issue.  
- Include as much detail as possible:
  - Hardware platform (e.g., Modalix DevKit, Davinci DevKit)  
  - OS/Yocto build version  
  - Steps to reproduce the issue  
  - Logs, error messages, or screenshots  

### 2. Submitting Changes
- Fork the repository and create a feature branch.  
- Follow coding standards:
  - Use **clear, descriptive commit messages**  
  - Keep commits focused and atomic  
  - Add comments for clarity in complex code paths  

- Ensure your changes:
  - Pass all tests (unit tests or repo-specific instructions)  
  - Build successfully (Yocto recipes, SDK tools, or demos)  
  - Include updated **docs** or **examples** if behavior changes  

- Open a **Pull Request (PR)**:
  - Reference related issues (e.g., `Fixes #42`)  
  - Complete the PR template checklist  
  - Be responsive to feedback during review  

### 3. Documentation Contributions
- All documentation is written in **Markdown** or **reStructuredText (RST)** depending on repo.  
- Ensure code snippets are copy-paste ready.  
- When adding new features, include usage examples in **README** or `docs/`.  

---

## Areas You Can Contribute

- **System Enablement**  
  - Yocto layers, recipes, and board bring-up for SiMa.ai MLSoC  
  - Kernel modules, drivers, and integration scripts  

- **AI Demos and Pipelines**  
  - Vision (detection, segmentation, classification)  
  - Vision + Language (VLM multimodal demos)  
  - Voice and audio-processing examples  

- **Developer Tools**  
  - CLI enhancements (`sima-cli`)  
  - Packaging, installers, and deployment helpers  

- **Documentation & Examples**  
  - Tutorials, guides, and best practices  
  - Improving clarity in setup instructions  

---

## Coding Style

- Follow **PEP8** for Python, **clang-format** for C/C++, and repository-specific linters.  
- Use **descriptive variable and function names**.  
- Avoid introducing platform-specific hacks unless gated by `#ifdef` or environment checks.  

---

## Security

If you discover a security vulnerability:
- **Do not open a public issue.**  
- Report privately via [support@sima.ai](mailto:support@sima.ai).  
- We will investigate promptly and work with you on disclosure.  

---

## Code of Conduct

All contributions are governed by our [Code of Conduct](./CODE_OF_CONDUCT.md).  
By participating, you agree to uphold a respectful and professional environment.  

---

## Thank You

Every contribution — whether a bug fix, feature, or docs improvement — helps make **Physical AI at the Edge** more accessible to developers worldwide.  

We deeply appreciate your support and collaboration.  
