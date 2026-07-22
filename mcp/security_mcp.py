from tools.secret_scanner import SecretScanner


class SecurityMCP:

    def __init__(self):
        self.scanner = SecretScanner()

    def scan(
        self,
        repo_path,
        repo_name,
        since_commit=None,
    ):

        report = self.scanner.scan(
            repo_path=repo_path,
            repo_name=repo_name,
            since_commit=since_commit,
        )

        return [
            f.to_dict()
            for f in report.findings
        ]