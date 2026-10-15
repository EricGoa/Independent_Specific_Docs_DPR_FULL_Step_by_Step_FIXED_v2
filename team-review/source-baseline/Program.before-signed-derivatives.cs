using Aspose.Pdf;

internal static class Program
{
    private const int ExitSuccess = 0;
    private const int ExitUsage = 2;
    private const int ExitConversionFailed = 3;
    private const int ExitRuntimeError = 4;

    public static int Main(string[] args)
    {
        try
        {
            var options = ParseArgs(args);
            if (options is null)
            {
                PrintUsage();
                return ExitUsage;
            }

            string input = Path.GetFullPath(options.Input);
            string output = Path.GetFullPath(options.Output);
            string log = Path.GetFullPath(options.Log);
            string licensePath = Path.GetFullPath(options.License);

            if (!File.Exists(input))
                throw new FileNotFoundException("Input PDF does not exist.", input);
            if (!File.Exists(licensePath))
                throw new FileNotFoundException("Aspose licence file does not exist.", licensePath);

            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            Directory.CreateDirectory(Path.GetDirectoryName(log)!);

            if (File.Exists(output))
                throw new IOException($"Refusing to overwrite existing master: {output}");

            var license = new Aspose.Pdf.License();
            license.SetLicense(licensePath);

            using var document = new Aspose.Pdf.Document(input);

            // Workflow rule: if the source is already exactly PDF/A-2b, do not
            // normalise it again. Create the canonical master as a byte-for-byte copy.
            if (document is { IsPdfaCompliant: true, PdfFormat: PdfFormat.PDF_A_2B })
            {
                File.Copy(input, output, overwrite: false);
                Console.WriteLine("STATUS=SKIPPED_ALREADY_PDF_A_2B");
                Console.WriteLine($"INPUT={input}");
                Console.WriteLine($"OUTPUT={output}");
                Console.WriteLine("TARGET=PDF/A-2b");
                return ExitSuccess;
            }

            // Strict PoC mode: None prevents Aspose from automatically deleting
            // elements that cannot be converted. The conversion can therefore fail,
            // which is exactly what we want to surface for later human review.
            bool converted = document.Convert(
                log,
                PdfFormat.PDF_A_2B,
                ConvertErrorAction.None
            );

            if (!converted)
            {
                if (File.Exists(output)) File.Delete(output);
                Console.Error.WriteLine("STATUS=CONVERSION_FAILED_STRICT");
                Console.Error.WriteLine($"INPUT={input}");
                Console.Error.WriteLine($"LOG={log}");
                return ExitConversionFailed;
            }

            document.Save(output);

            Console.WriteLine("STATUS=CONVERTED");
            Console.WriteLine($"INPUT={input}");
            Console.WriteLine($"OUTPUT={output}");
            Console.WriteLine($"LOG={log}");
            Console.WriteLine("TARGET=PDF/A-2b");
            return ExitSuccess;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("STATUS=RUNTIME_ERROR");
            Console.Error.WriteLine(ex.ToString());
            return ExitRuntimeError;
        }
    }

    private static Options? ParseArgs(string[] args)
    {
        string? input = null;
        string? output = null;
        string? log = null;
        string? license = null;

        for (int i = 0; i < args.Length; i++)
        {
            string arg = args[i];
            string? value = i + 1 < args.Length ? args[i + 1] : null;

            switch (arg)
            {
                case "--input":
                    input = value; i++; break;
                case "--output":
                    output = value; i++; break;
                case "--log":
                    log = value; i++; break;
                case "--license":
                    license = value; i++; break;
                case "--help":
                case "-h":
                    return null;
                default:
                    Console.Error.WriteLine($"Unknown argument: {arg}");
                    return null;
            }
        }

        return string.IsNullOrWhiteSpace(input) ||
               string.IsNullOrWhiteSpace(output) ||
               string.IsNullOrWhiteSpace(log) ||
               string.IsNullOrWhiteSpace(license)
            ? null
            : new Options(input, output, log, license);
    }

    private static void PrintUsage()
    {
        Console.WriteLine(
            "Usage: DprPdfaConverter --input <source.pdf> --output <master.pdf> " +
            "--log <conversion.xml> --license <Aspose.lic>"
        );
    }

    private sealed record Options(string Input, string Output, string Log, string License);
}
