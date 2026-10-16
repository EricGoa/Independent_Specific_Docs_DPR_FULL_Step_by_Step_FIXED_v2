using Aspose.Pdf;
using Aspose.Pdf.Facades;

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

            using var original = new Aspose.Pdf.Document(input);

            // Workflow rule: if the source is already exactly PDF/A-2b, do not
            // normalise it again. Create the canonical master as a byte-for-byte copy.
            if (original is { IsPdfaCompliant: true, PdfFormat: PdfFormat.PDF_A_2B })
            {
                File.Copy(input, output, overwrite: false);
                Console.WriteLine("STATUS=SKIPPED_ALREADY_PDF_A_2B");
                Console.WriteLine($"INPUT={input}");
                Console.WriteLine($"OUTPUT={output}");
                Console.WriteLine("TARGET=PDF/A-2b");
                return ExitSuccess;
            }

            // Explicitly authorized derivative policy. Never save over the signed
            // original; remove cryptographic signatures only in an in-memory copy.
            using var unsignedBuffer = new MemoryStream();
            int removedSignatures = 0;
            if (options.AllowSignedDerivative)
            {
                using var signer = new PdfFileSignature();
                signer.BindPdf(input);
                var names = signer.GetSignatureNames();
                removedSignatures = names.Count;
                foreach (var name in names)
                    signer.RemoveSignature(name, false);
                if (removedSignatures > 0)
                {
                    signer.Save(unsignedBuffer);
                    unsignedBuffer.Position = 0;
                    Console.WriteLine($"SIGNATURES_REMOVED_IN_DERIVATIVE={removedSignatures}");
                    Console.WriteLine("SIGNED_ORIGINAL_RETAINED=true");
                }
            }
            using var unsignedDocument = removedSignatures > 0
                ? new Aspose.Pdf.Document(unsignedBuffer) : null;
            var document = unsignedDocument ?? original;

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
            // Serialize newly created XML evidence with a declaration so DROID
            // can identify it. Engine report body remains byte-for-byte intact.
            if (System.IO.File.Exists(log))
            {
                var logBody = System.IO.File.ReadAllText(log, System.Text.Encoding.UTF8);
                System.Xml.Linq.XDocument.Parse(logBody);
                if (!logBody.TrimStart().StartsWith("<?xml", StringComparison.Ordinal))
                {
                    Console.WriteLine("LOG_BODY_SHA512=" + Convert.ToHexString(
                        System.Security.Cryptography.SHA512.HashData(System.Text.Encoding.UTF8.GetBytes(logBody))).ToLowerInvariant());
                    System.IO.File.WriteAllText(log, "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" + logBody,
                        new System.Text.UTF8Encoding(false));
                    Console.WriteLine("LOG_SERIALIZATION=XML_DECLARATION_ADDED_BODY_UNCHANGED");
                }
            }

            Console.WriteLine(removedSignatures > 0 ? "STATUS=CONVERTED_UNSIGNED_DERIVATIVE" : "STATUS=CONVERTED");
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
        bool allowSignedDerivative = false;

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
                case "--allow-signed-derivative":
                    allowSignedDerivative = true; break;
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
            : new Options(input, output, log, license, allowSignedDerivative);
    }

    private static void PrintUsage()
    {
        Console.WriteLine(
            "Usage: DprPdfaConverter --input <source.pdf> --output <master.pdf> " +
            "--log <conversion.xml> --license <Aspose.lic>"
        );
    }

    private sealed record Options(string Input, string Output, string Log, string License, bool AllowSignedDerivative);
}
