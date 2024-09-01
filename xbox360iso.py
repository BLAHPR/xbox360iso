import binascii
import io
import os
from struct import unpack
import sys
import csv

def get_data_file_path(filename):
    if hasattr(sys, '_MEIPASS'):
        # If running as a bundled executable
        return os.path.join(sys._MEIPASS, filename)
    else:
        # If running as a script
        return filename

class Xbox360ISO(object):
    def __init__(self):
        self.iso_type = {'GDF': 0xfd90000,
                         'XGD3': 0x2080000,
                         'XSF': 0}
        self.csv_file = get_data_file_path('xbox360_gamelist.csv')
        self.game_lookup = self.load_game_lookup()

    def load_game_lookup(self):
        game_lookup = {}
        if not os.path.isfile(self.csv_file):
            print(f"CSV file not found: {self.csv_file}")
            return game_lookup

        try:
            with open(self.csv_file, mode='r', newline='', encoding='utf-8') as csvfile:
                reader = csv.reader(csvfile)
                for row in reader:
                    if len(row) > 1:
                        game_name = row[0].strip()
                        title_id = row[1].strip().upper()
                        game_lookup[title_id] = game_name
        except Exception as e:
            print(f"Failed to read CSV file: {e}")

        return game_lookup

    def parse(self, filename):
        try:
            with open(filename, "rb") as iso_file:
                iso_info = self.check_iso(iso_file)
                if iso_info is False:
                    return None

                xex_buffer = self.extract_defaultxex(iso_file, iso_info)
                if xex_buffer is False:
                    return None

                xex_info = self.extract_xex_info(xex_buffer)
                if xex_info is False:
                    return None

                # Fetch game name from the loaded game lookup dictionary
                title_id = xex_info.get('title_id', 'Unknown')
                xex_info['game_name'] = self.game_lookup.get(title_id, "Unknown")

                props = iso_info.copy()
                props.update(xex_info)

                return props
        except Exception as e:
            print(f"Failed to parse {filename}: {e}")
            return None

    def check_iso(self, iso_file):
        iso_info = {'sector_size': 0x800}
        iso_file.seek((0x20 * iso_info['sector_size']))
        if iso_file.read(20).decode("ascii", "ignore") == 'MICROSOFT*XBOX*MEDIA':
            iso_info['root_offset'] = self.iso_type['XSF']
            print('Original Xbox ISO format not supported')
            return False
        else:
            iso_file.seek((0x20 * iso_info['sector_size']) + self.iso_type['GDF'])
            if iso_file.read(20).decode("ascii", "ignore") == 'MICROSOFT*XBOX*MEDIA':
                iso_info['root_offset'] = self.iso_type['GDF']
            else:
                iso_file.seek((0x20 * iso_info['sector_size']) + self.iso_type['XGD3'])
                if iso_file.read(20).decode("ascii", "ignore") == 'MICROSOFT*XBOX*MEDIA':
                    iso_info['root_offset'] = self.iso_type['XGD3']
                else:
                    print('Unknown ISO format')
                    return False
        iso_file.seek((0x20 * iso_info['sector_size']) + iso_info['root_offset'])
        iso_info['identifier'] = iso_file.read(20).decode("ascii", "ignore")
        iso_info['root_dir_sector'] = unpack('I', iso_file.read(4))[0]
        iso_info['root_dir_size'] = unpack('I', iso_file.read(4))[0]
        iso_info['image_size'] = os.fstat(iso_file.fileno()).st_size
        iso_info['volume_size'] = iso_info['image_size'] - iso_info['root_offset']
        iso_info['volume_sectors'] = iso_info['volume_size'] / iso_info['sector_size']
        return iso_info

    @staticmethod
    def extract_defaultxex(iso_file, iso_info):
        iso_file.seek((iso_info['root_dir_sector'] * iso_info['sector_size']) + iso_info['root_offset'])
        root_sector_buffer = io.BytesIO()
        root_sector_buffer.write(iso_file.read(iso_info['root_dir_size']))
        root_sector_buffer.seek(0)

        for i in range(0, iso_info['root_dir_size'] - 12):
            root_sector_buffer.seek(i)
            root_sector_buffer.read(1)
            if int.from_bytes(root_sector_buffer.read(1), byteorder='big') == 11:
                if root_sector_buffer.read(11).decode("ascii", "ignore").lower() == 'default.xex':
                    root_sector_buffer.seek(i - 8)
                    file_sector = unpack('I', root_sector_buffer.read(4))[0]
                    file_size = unpack('I', root_sector_buffer.read(4))[0]

                    iso_file.seek(iso_info['root_offset'] + (file_sector * iso_info['sector_size']))
                    xex_buffer = io.BytesIO()
                    xex_buffer.write(iso_file.read(file_size))
                    xex_buffer.seek(0)
                    return xex_buffer
        print('default.xex not found')
        return False

    @staticmethod
    def extract_xex_info(xex_buffer):
        xex_info = {}

        xex_buffer.seek(0)
        if xex_buffer.read(4).decode("ascii", "ignore") == 'XEX2':
            xex_buffer.seek(0x08)
            code_offset = unpack('>I', xex_buffer.read(4))[0]
            if code_offset > sys.getsizeof(xex_buffer):
                print('Starting address of Xex code is beyond size of default.xex')
                return False

            xex_buffer.seek(0x10)
            cert_offset = unpack('>I', xex_buffer.read(4))[0]
            if cert_offset > code_offset:
                print('Xex certificate offset is beyond the starting address of Xex code')
                return False

            xex_buffer.seek(0x14)
            info_table_num_entries = unpack('>I', xex_buffer.read(4))[0]
            if info_table_num_entries * 8 + 24 > code_offset:
                print('Xex general info table has entries that spill over into the Xex code')
                return False

            execution_info_address = False
            execution_info_table_flags = bytes([0x00, 0x04, 0x00, 0x06])

            for i in range(0, info_table_num_entries):
                header_id = unpack('>I', xex_buffer.read(4))[0]

                if header_id == unpack('>I', execution_info_table_flags)[0]:
                    execution_info_address = unpack('>I', xex_buffer.read(4))[0]
                else:
                    xex_buffer.read(4)

            if execution_info_address is not False:
                xex_buffer.seek(execution_info_address)
                xex_info['media_id'] = binascii.hexlify(xex_buffer.read(4)).decode("ascii", "ignore").upper()
                xex_info['version'] = unpack('>I', xex_buffer.read(4))[0]
                xex_info['base_version'] = unpack('>I', xex_buffer.read(4))[0]
                xex_info['title_id'] = binascii.hexlify(xex_buffer.read(4)).decode("ascii", "ignore").upper()
                xex_info['platform'] = ord(xex_buffer.read(1))
                xex_info['executable_type'] = ord(xex_buffer.read(1))
                xex_info['disc_number'] = ord(xex_buffer.read(1))
                xex_info['disc_count'] = ord(xex_buffer.read(1))
            else:
                return False

            return xex_info
        else:
            print('XEX2 was not found at the start of default.xex')
            return False

def main():
    iso_parser = Xbox360ISO()
    current_dir = os.getcwd()
    iso_files = [f for f in os.listdir(current_dir) if f.lower().endswith('.iso')]

    all_info = []

    print(f"Found {len(iso_files)} ISO(s) in {current_dir}\n")

    for iso in iso_files:
        print(f"-> {iso}")
        info = iso_parser.parse(iso)
        if info:
            # Add ISO file name to the info dictionary
            info['iso_name'] = iso
            all_info.append(info)
            print(f"Game Name: {info.get('game_name', 'Unknown')}")
            print(f"Media ID: {info.get('media_id', 'N/A')}")
            print(f"Title ID: {info.get('title_id', 'N/A')}")
            print(f"Disc Number: {info.get('disc_number', 'N/A')} of {info.get('disc_count', 'N/A')}")
            print("-" * 40)
        else:
            print(f"Failed to process: {iso}")
            print("-" * 40)

    if all_info:
        base_filename = "GameInfo"
        counter = 1
        filename = f"{base_filename}.txt"
        while os.path.isfile(filename):
            filename = f"{base_filename}_{counter}.txt"
            counter += 1

        with open(filename, "w") as file:
            for info in all_info:
                file.write(f"-> {info.get('iso_name', 'Unknown')}\n")
                file.write(f"Game Name: {info.get('game_name', 'Unknown')}\n")
                file.write(f"Media ID: {info.get('media_id', 'N/A')}\n")
                file.write(f"Title ID: {info.get('title_id', 'N/A')}\n")
                file.write(f"Disc Number: {info.get('disc_number', 'N/A')} of {info.get('disc_count', 'N/A')}\n")
                file.write("-" * 40 + "\n")

        print(f"Game information saved to {filename}")

    print("Press Enter to Scan Again...")
    input()  # Waits for user to press Enter

if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print("\nExiting...")
            break
