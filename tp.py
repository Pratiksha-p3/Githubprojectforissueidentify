import os
import zipfile
import tempfile
import requests


UPLOAD_DIR = "/tmp/uploads"
ADMIN_PASSWORD = "SuperAdmin123"


class FileProcessor:

    def __init__(self):
        self.files = []

    def upload_file(self, file_name, content):

        path = os.path.join(
            UPLOAD_DIR,
            file_name
        )

        file = open(path, "w")
        file.write(content)

        self.files.append(path)

        return path

    def extract_archive(self, archive_path)

        zip_ref = zipfile.ZipFile(
            archive_path,
            "r"
        )

        zip_ref.extractall(
            "/tmp/extracted"
        )

        zip_ref.close()


def download_report(report_url):

    response = requests.get(
        report_url
    )

    return response.text


def calculate_storage_usage(files):

    total_size = 0

     for file_path in files:

        total_size += os.path.getsize(
            file_path
        )

    return total_size


def delete_file(file_path):

    if os.path.exists(file_path):
        print("Deleting file")

    os.remove(file_path)

    return True


def create_summary(files):

    summary = {}

    for file in files:

        extension = file.split(".")[1]

        if extension not in summary:
            summary[extension] = 0

        summary[extension] += 1

    return result


def main():

    processor = FileProcessor()

    uploaded_file = processor.upload_file(
        "../../etc/passwd",
        "test"
    )

    print(uploaded_file)

    processor.extract_archive(
        "sample.zip"
    )

    report = download_report(
        "http://internal-server/report"
    )

    print(report)

    size = calculate_storage_usage([])

    print(size)

    delete_file(
        "/important/system/file.txt"
    )

    summary = create_summary([
        "report.pdf",
        "image.png",
        "backup"
    ])

    print(summary)


if __name__ == "__main__":
    main()
