import katex from "katex";
import { describe, expect, it } from "vitest";
import corpusData from "../../../../evals/board/lesson-questions.json";

type Subject = "calculus" | "physics" | "ece" | "chemistry" | "statistics";
type NumericInputs = Record<string, number | number[] | undefined>;
type Question = {
  id: string;
  subject: Subject;
  topic: string;
  framing: string;
  prompt: string;
  assumptions: string[];
  finalAnswer: {
    text: string;
    latex: string;
    numeric: number;
    unit: string;
    absoluteTolerance: number;
  };
  verification: {
    method: string;
    family: string;
    inputs: NumericInputs;
    inputUnits: Record<string, string | undefined>;
  };
};

const questions = corpusData.questions as Question[];
const subjectPrefixes: Record<Subject, string> = {
  calculus: "calc",
  physics: "phys",
  ece: "ece",
  chemistry: "chem",
  statistics: "stat",
};
const families: Record<
  string,
  { subject: Subject; unit: string; inputs: Record<string, string> }
> = {
  "derivative-power": {
    subject: "calculus",
    unit: "1",
    inputs: { coefficient: "1", power: "1", point: "1" },
  },
  "integral-power": {
    subject: "calculus",
    unit: "1",
    inputs: { coefficient: "1", power: "1", lower: "1", upper: "1" },
  },
  "integral-x-exponential": {
    subject: "calculus",
    unit: "1",
    inputs: { coefficient: "1", upper: "1" },
  },
  "derivative-log-linear": {
    subject: "calculus",
    unit: "1",
    inputs: { coefficient: "1", constant: "1", point: "1" },
  },
  "derivative-sine": {
    subject: "calculus",
    unit: "1",
    inputs: { frequency: "1", pointRadians: "rad" },
  },
  "net-force": {
    subject: "physics",
    unit: "N",
    inputs: { massKg: "kg", accelerationMps2: "m/s^2" },
  },
  "kinetic-energy": {
    subject: "physics",
    unit: "J",
    inputs: { massKg: "kg", speedMps: "m/s" },
  },
  "constant-speed-distance": {
    subject: "physics",
    unit: "m",
    inputs: { speedMps: "m/s", timeS: "s" },
  },
  "gravitational-energy-change": {
    subject: "physics",
    unit: "J",
    inputs: { massKg: "kg", gravityMps2: "m/s^2", heightChangeM: "m" },
  },
  "linear-momentum": {
    subject: "physics",
    unit: "kg m/s",
    inputs: { massKg: "kg", velocityMps: "m/s" },
  },
  "resistor-current": {
    subject: "ece",
    unit: "A",
    inputs: { voltageV: "V", resistanceOhm: "ohm" },
  },
  "voltage-divider": {
    subject: "ece",
    unit: "V",
    inputs: {
      inputV: "V",
      topResistanceOhm: "ohm",
      bottomResistanceOhm: "ohm",
    },
  },
  "rc-time-constant": {
    subject: "ece",
    unit: "s",
    inputs: { resistanceOhm: "ohm", capacitanceF: "F" },
  },
  "unsigned-binary": {
    subject: "ece",
    unit: "1",
    inputs: { bits: "bit" },
  },
  "inverting-amplifier": {
    subject: "ece",
    unit: "V",
    inputs: {
      feedbackResistanceOhm: "ohm",
      inputResistanceOhm: "ohm",
      inputV: "V",
    },
  },
  "mass-to-moles": {
    subject: "chemistry",
    unit: "mol",
    inputs: { massG: "g", molarMassGPerMol: "g/mol" },
  },
  "ideal-gas-pressure": {
    subject: "chemistry",
    unit: "kPa",
    inputs: {
      amountMol: "mol",
      temperatureK: "K",
      volumeL: "L",
      gasConstantKpaLPerMolK: "kPa L/(mol K)",
    },
  },
  "dilution-concentration": {
    subject: "chemistry",
    unit: "mol/L",
    inputs: {
      initialConcentrationMolPerL: "mol/L",
      aliquotMl: "mL",
      finalVolumeMl: "mL",
    },
  },
  molarity: {
    subject: "chemistry",
    unit: "mol/L",
    inputs: { amountMol: "mol", solutionVolumeL: "L" },
  },
  "stoichiometric-product-mass": {
    subject: "chemistry",
    unit: "g",
    inputs: {
      reactantMol: "mol",
      reactantCoefficient: "1",
      productCoefficient: "1",
      productMolarMassGPerMol: "g/mol",
    },
  },
  "arithmetic-mean": {
    subject: "statistics",
    unit: "points",
    inputs: { values: "points" },
  },
  "population-variance": {
    subject: "statistics",
    unit: "points^2",
    inputs: { values: "points" },
  },
  "sample-variance": {
    subject: "statistics",
    unit: "points^2",
    inputs: { values: "points" },
  },
  "binomial-exact-probability": {
    subject: "statistics",
    unit: "1",
    inputs: { trials: "1", successes: "1", successProbability: "1" },
  },
  "standard-score": {
    subject: "statistics",
    unit: "1",
    inputs: { score: "points", mean: "points", standardDeviation: "points" },
  },
};

function finiteNumber(value: unknown): asserts value is number {
  expect(typeof value).toBe("number");
  expect(Number.isFinite(value)).toBe(true);
  expect(Math.abs(value as number)).toBeLessThanOrEqual(1_000_000_000);
}

function scalar(inputs: NumericInputs, key: string, positive = false): number {
  const value = inputs[key];
  finiteNumber(value);
  if (positive) expect(value).toBeGreaterThan(0);
  return value;
}

function list(inputs: NumericInputs, key: string): number[] {
  const value = inputs[key];
  expect(Array.isArray(value)).toBe(true);
  if (!Array.isArray(value)) throw new Error(`Expected numeric array: ${key}`);
  expect(value.length).toBeGreaterThanOrEqual(2);
  expect(value.length).toBeLessThanOrEqual(12);
  for (const item of value) finiteNumber(item);
  return value;
}

// These calculations read numeric inputs only. They do not inspect the stored
// final answer, execute a prompt, or run a lesson model/provider.
function recompute(question: Question): number {
  const inputs = question.verification.inputs;
  const n = (key: string, positive = false) => scalar(inputs, key, positive);
  switch (question.verification.family) {
    case "derivative-power": {
      const power = n("power", true);
      expect(Number.isInteger(power)).toBe(true);
      expect(power).toBeLessThanOrEqual(8);
      return n("coefficient") * power * n("point") ** (power - 1);
    }
    case "integral-power": {
      const power = n("power");
      expect(Number.isInteger(power)).toBe(true);
      expect(power).toBeGreaterThanOrEqual(0);
      expect(power).toBeLessThanOrEqual(8);
      expect(n("upper")).toBeGreaterThan(n("lower"));
      const primitive = (x: number) =>
        (n("coefficient") * x ** (power + 1)) / (power + 1);
      return primitive(n("upper")) - primitive(n("lower"));
    }
    case "integral-x-exponential": {
      const upper = n("upper", true);
      expect(upper).toBeLessThanOrEqual(8);
      const primitive = (x: number) => Math.exp(x) * (x - 1);
      return n("coefficient") * (primitive(upper) - primitive(0));
    }
    case "derivative-log-linear": {
      const argument = n("coefficient") * n("point") + n("constant");
      expect(argument).toBeGreaterThan(0);
      return n("coefficient") / argument;
    }
    case "derivative-sine":
      return (
        n("frequency", true) * Math.cos(n("frequency") * n("pointRadians"))
      );
    case "net-force":
      return n("massKg", true) * n("accelerationMps2");
    case "kinetic-energy":
      expect(n("speedMps")).toBeGreaterThanOrEqual(0);
      return (n("massKg", true) * n("speedMps") ** 2) / 2;
    case "constant-speed-distance":
      expect(n("speedMps")).toBeGreaterThanOrEqual(0);
      return n("speedMps") * n("timeS", true);
    case "gravitational-energy-change":
      return n("massKg", true) * n("gravityMps2", true) * n("heightChangeM");
    case "linear-momentum":
      return n("massKg", true) * n("velocityMps");
    case "resistor-current":
      return n("voltageV") / n("resistanceOhm", true);
    case "voltage-divider":
      return (
        n("inputV") /
        (1 + n("topResistanceOhm", true) / n("bottomResistanceOhm", true))
      );
    case "rc-time-constant":
      return n("resistanceOhm", true) * n("capacitanceF", true);
    case "unsigned-binary": {
      const bits = list(inputs, "bits");
      for (const bit of bits) expect([0, 1]).toContain(bit);
      return bits.reduce((integer, bit) => integer * 2 + bit, 0);
    }
    case "inverting-amplifier":
      return (
        (-n("feedbackResistanceOhm", true) / n("inputResistanceOhm", true)) *
        n("inputV")
      );
    case "mass-to-moles":
      return n("massG", true) / n("molarMassGPerMol", true);
    case "ideal-gas-pressure":
      expect(n("gasConstantKpaLPerMolK")).toBe(8.314462618);
      return (
        (n("amountMol", true) *
          n("temperatureK", true) *
          n("gasConstantKpaLPerMolK", true)) /
        n("volumeL", true)
      );
    case "dilution-concentration":
      expect(n("finalVolumeMl", true)).toBeGreaterThan(n("aliquotMl", true));
      return (
        n("initialConcentrationMolPerL", true) *
        (n("aliquotMl") / n("finalVolumeMl"))
      );
    case "molarity":
      return n("amountMol", true) / n("solutionVolumeL", true);
    case "stoichiometric-product-mass":
      expect(Number.isInteger(n("reactantCoefficient", true))).toBe(true);
      expect(Number.isInteger(n("productCoefficient", true))).toBe(true);
      return (
        (n("reactantMol", true) / n("reactantCoefficient")) *
        n("productCoefficient") *
        n("productMolarMassGPerMol", true)
      );
    case "arithmetic-mean": {
      const values = list(inputs, "values");
      return values.reduce((sum, value) => sum + value, 0) / values.length;
    }
    case "population-variance":
    case "sample-variance": {
      const values = list(inputs, "values");
      // Pairwise squared differences provide an independent variance formula:
      // sum(i<j)(xi-xj)^2 = N * sum(i)(xi-mean)^2.
      let pairwiseSquares = 0;
      for (let i = 0; i < values.length; i += 1) {
        for (let j = i + 1; j < values.length; j += 1) {
          pairwiseSquares += (values[i] - values[j]) ** 2;
        }
      }
      const divisor =
        question.verification.family === "sample-variance"
          ? values.length - 1
          : values.length;
      return pairwiseSquares / (values.length * divisor);
    }
    case "binomial-exact-probability": {
      const trials = n("trials", true);
      const successes = n("successes");
      const probability = n("successProbability");
      expect(Number.isInteger(trials)).toBe(true);
      expect(trials).toBeLessThanOrEqual(20);
      expect(Number.isInteger(successes)).toBe(true);
      expect(successes).toBeGreaterThanOrEqual(0);
      expect(successes).toBeLessThanOrEqual(trials);
      expect(probability).toBeGreaterThanOrEqual(0);
      expect(probability).toBeLessThanOrEqual(1);
      // Dynamic programming avoids sharing the stored answers' combinations
      // formula and covers the zero/all-success boundary cases.
      let distribution = [1];
      for (let trial = 0; trial < trials; trial += 1) {
        const next = Array<number>(distribution.length + 1).fill(0);
        for (let count = 0; count < distribution.length; count += 1) {
          next[count] += distribution[count] * (1 - probability);
          next[count + 1] += distribution[count] * probability;
        }
        distribution = next;
      }
      return distribution[successes];
    }
    case "standard-score":
      return (n("score") - n("mean")) / n("standardDeviation", true);
    default:
      throw new Error(
        `Unsupported verification family: ${question.verification.family}`,
      );
  }
}

// A deliberately closed numeric notation parser, not a general LaTeX evaluator.
// This cross-checks the answer displayed to evaluators against its numeric key.
function numericAnswerLatex(latex: string): number {
  const fraction = /^(-?)\\frac\{(\d+)\}\{([1-9]\d*)\}$/.exec(latex);
  if (fraction) {
    return (
      ((fraction[1] === "-" ? -1 : 1) * Number(fraction[2])) /
      Number(fraction[3])
    );
  }
  const exponential = /^(\d+)e\^\{(\d+)\}\+(\d+)$/.exec(latex);
  if (exponential) {
    return (
      Number(exponential[1]) * Math.exp(Number(exponential[2])) +
      Number(exponential[3])
    );
  }
  if (/^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$/i.test(latex)) return Number(latex);
  throw new Error(`Unsupported expected-answer notation: ${latex}`);
}

function exactKeys(value: object, keys: string[]) {
  expect(Object.keys(value).sort()).toEqual([...keys].sort());
}

function boundedText(value: string, minimum = 1, maximum = 500) {
  expect(typeof value).toBe("string");
  expect(value.trim().length).toBeGreaterThanOrEqual(minimum);
  expect(value.length).toBeLessThanOrEqual(maximum);
  expect([...value].every((character) => character.charCodeAt(0) >= 32)).toBe(
    true,
  );
  expect(value).not.toMatch(/[<>]/);
  expect(value).not.toMatch(/\b(?:todo|tbd|placeholder|lorem ipsum)\b/i);
}

describe("Board Builder public synthetic practice corpus integrity", () => {
  it("keeps qualification separate from model performance and learning claims", () => {
    expect(corpusData.schemaVersion).toBe(1);
    expect(corpusData.id).toBe("navox-board-practice-v1");
    expect(corpusData.provenance.kind).toBe("original-synthetic-practice");
    expect(corpusData.qualification.scope).toBe("Corpus integrity only.");
    expect(corpusData.qualification.lessonWriterEvaluated).toBe(false);
    expect(corpusData.qualification.educationalEffectivenessEvaluated).toBe(
      false,
    );
    expect(corpusData.provenance.answerStatus).toContain(
      "review remains a release gate",
    );
  });

  it("has 100 unique questions, exactly 20 per subject and four per family", () => {
    expect(questions).toHaveLength(100);
    expect(new Set(questions.map((question) => question.id)).size).toBe(100);
    expect(
      new Set(questions.map((question) => question.prompt.toLowerCase().trim()))
        .size,
    ).toBe(100);
    expect(new Set(questions.map((question) => question.subject))).toEqual(
      new Set(Object.keys(subjectPrefixes)),
    );
    expect(
      new Set(questions.map((question) => question.verification.family)),
    ).toEqual(new Set(Object.keys(families)));
    for (const [subject, prefix] of Object.entries(subjectPrefixes)) {
      const subset = questions.filter(
        (question) => question.subject === subject,
      );
      expect(subset).toHaveLength(20);
      expect(subset.map((question) => question.id)).toEqual(
        Array.from(
          { length: 20 },
          (_, index) => `${prefix}-${String(index + 1).padStart(3, "0")}`,
        ),
      );
    }
    for (const family of Object.keys(families)) {
      expect(
        questions.filter((question) => question.verification.family === family),
      ).toHaveLength(4);
    }
  });

  it.each(questions)(
    "$id has a bounded, reproducible answer key",
    (question) => {
      exactKeys(question, [
        "id",
        "subject",
        "topic",
        "framing",
        "prompt",
        "assumptions",
        "finalAnswer",
        "verification",
      ]);
      expect(question.id).toMatch(/^(calc|phys|ece|chem|stat)-\d{3}$/);
      expect(question.framing).toBe("synthetic-practice");
      boundedText(question.topic, 5, 80);
      boundedText(question.prompt, 60, 500);
      expect(question.prompt.startsWith("Practice:")).toBe(true);
      expect(question.assumptions.length).toBeGreaterThanOrEqual(1);
      expect(question.assumptions.length).toBeLessThanOrEqual(4);
      for (const assumption of question.assumptions)
        boundedText(assumption, 20, 300);
      exactKeys(question.verification, [
        "method",
        "family",
        "inputs",
        "inputUnits",
      ]);
      expect(question.verification.method).toBe("closed-form-numeric");
      const family = families[question.verification.family];
      expect(family).toBeDefined();
      expect(question.subject).toBe(family.subject);
      exactKeys(question.verification.inputs, Object.keys(family.inputs));
      expect(question.verification.inputUnits).toEqual(family.inputs);
      exactKeys(question.finalAnswer, [
        "text",
        "latex",
        "numeric",
        "unit",
        "absoluteTolerance",
      ]);
      boundedText(question.finalAnswer.text, 1, 100);
      boundedText(question.finalAnswer.latex, 1, 160);
      finiteNumber(question.finalAnswer.numeric);
      expect(question.finalAnswer.unit).toBe(family.unit);
      expect(question.finalAnswer.absoluteTolerance).toBe(1e-9);
      const computed = recompute(question);
      finiteNumber(computed);
      expect(
        Math.abs(question.finalAnswer.numeric - computed),
      ).toBeLessThanOrEqual(question.finalAnswer.absoluteTolerance);
      expect(
        Math.abs(numericAnswerLatex(question.finalAnswer.latex) - computed),
      ).toBeLessThanOrEqual(question.finalAnswer.absoluteTolerance);
      const displayedNumeric = Number(question.finalAnswer.text.split(" ")[0]);
      expect(Math.abs(displayedNumeric - computed)).toBeLessThanOrEqual(
        question.finalAnswer.absoluteTolerance,
      );
      expect(question.finalAnswer.text).toContain(
        family.unit === "1" ? "(dimensionless)" : ` ${family.unit}`,
      );
      expect(() =>
        katex.renderToString(question.finalAnswer.latex, {
          trust: false,
          throwOnError: true,
          strict: "error",
          maxExpand: 100,
          maxSize: 12,
          macros: Object.create(null),
        }),
      ).not.toThrow();
    },
  );
});
