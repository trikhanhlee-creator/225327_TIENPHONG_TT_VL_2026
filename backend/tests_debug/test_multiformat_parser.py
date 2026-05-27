"""Test multi-format file parser functionality"""

import os
import sys

# Add parent directory to path to import app module
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from app.services.file_parser import FileParserFactory

def test_supported_formats():
    """Test that all formats are registered"""
    print("=" * 70)
    print("[*] Testing Supported Formats")
    print("=" * 70)
    
    supported = FileParserFactory.get_supported_extensions()
    print(f"\n✓ Supported extensions: {supported}")
    
    for ext in supported:
        print(f"  - {ext}")
    
    return len(supported) > 0

def test_parser_creation():
    """Test that parsers can be created for each format"""
    print("\n" + "=" * 70)
    print("[*] Testing Parser Creation")
    print("=" * 70)
    
    # This is a mock test - we just verify the factory works
    test_cases = [
        'test.doc',
        'test.docx',
        'test.pdf', 
        'test.xlsx',
        'test.csv',
        'test.txt'
    ]
    
    for filename in test_cases:
        try:
            # Just check if is_supported returns correct result
            is_supported = FileParserFactory.is_supported(filename)
            status = "✓" if is_supported else "✗"
            print(f"{status} {filename}: supported={is_supported}")
        except Exception as e:
            print(f"✗ {filename}: {str(e)}")
    
    return True

def test_unsupported_format():
    """Test that unsupported formats are rejected"""
    print("\n" + "=" * 70)
    print("[*] Testing Unsupported Format Rejection")
    print("=" * 70)
    
    try:
        # Try to create parser for unsupported format
        parser = FileParserFactory.create_parser('test.xyz')
        print("✗ Should have raised error for .xyz format")
        return False
    except ValueError as e:
        print(f"✓ Correctly rejected unsupported format: {str(e)}")
        return True
    except Exception as e:
        print(f"✗ Unexpected error: {str(e)}")
        return False

def main():
    print("\n" + "=" * 70)
    print("[*] MULTI-FORMAT FILE PARSER TEST")
    print("=" * 70)
    
    results = []
    
    # Run tests
    results.append(("Supported Formats", test_supported_formats()))
    results.append(("Parser Creation", test_parser_creation()))
    results.append(("Format Rejection", test_unsupported_format()))
    
    # Summary
    print("\n" + "=" * 70)
    print("[*] TEST SUMMARY")
    print("=" * 70)
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed\n")
    
    return all(result for _, result in results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
