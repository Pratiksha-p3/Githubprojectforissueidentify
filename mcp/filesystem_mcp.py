from pathlib import Path


class FilesystemMCP:

    def read_file(
        self,
        path: str,
    ) -> str:

        return Path(path).read_text(
            encoding="utf-8"
        )

    def write_file(
        self,
        path: str,
        content: str,
    ):

        Path(path).write_text(
            content,
            encoding="utf-8"
        )

    def apply_patch(
        self,
        path: str,
        old_code: str,
        new_code: str,
    ):

        content = self.read_file(path)

        updated = content.replace(
            old_code,
            new_code,
            1,
        )

        self.write_file(
            path,
            updated,
        )

        return True