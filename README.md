# App Fixtures

A Pennsieve platform app that builds another app's test inputs, as that app
declares them in the `review` block of its `app.yml`, and writes them laid out
exactly as the app reads its input directory. It is the first step of a
**sample run**: the sampled app depends on this step and nothing else, so the
platform hands it this step's output as its `INPUT_DIR`, unchanged. Nothing is
staged from a dataset and nothing persists after the run.

## What it does

Given a repository URL and a release tag:

1. Clones the repository at the tag (shallow) and reads its `app.yml`:
   the input ports (name, `placement`, `required`) and the `review` block.
2. Builds each port from its source, as
   [app-manifest-review.md](https://github.com/Pennsieve/app-deploy-service/blob/main/docs/app-manifest-review.md)
   defines them:
   - the top-level `review.generate`: one command that writes the whole input
     directory into `$REVIEW_INPUT_DIR` (its `root` folder is taken), for every
     port that has no source of its own;
   - `review.inputs.<port>.generate`: a command that writes that port's data;
   - `review.inputs.<port>.files`: files or globs committed in the repository,
     keeping their paths below the pattern's fixed prefix;
   - `review.inputs.<port>.dataset`: a public dataset by DOI or HTTPS URL on
     Zenodo, Figshare, OSF or OpenNeuro, narrowed to `files` patterns, within
     `MAX_DATASET_BYTES`. A generator's `source` is fetched the same way and
     mounted at `$REVIEW_SOURCE_DIR`.
3. Lays the result out as the app's `INPUT_DIR`: ports with
   `placement: subfolder` in `<port>/`, the rest at the root.
4. Checks that every required port received files. An optional port without
   a source, or whose generator wrote nothing, is left out.
5. Writes `.pennsieve-fixtures/ro-crate-metadata.json`, an RO-Crate 1.1
   describing each port's provenance (generator command, repository patterns,
   or the dataset's DOI and file checksums) and the app's repository, tag and
   commit, and prints the same as one `FIXTURES_PROVENANCE {...}` line so a
   service can read it from the step's log.

Generators run with `sh -c` from the repository root, in this image, which
carries the libraries the review docs promise generators (numpy, pandas,
scipy, h5py, anndata, zarr, scanpy, FlowIO, edfio, nibabel, pyarrow,
openpyxl). The `r` generator image is not available yet; a block that asks
for it fails with a clear message.

Unlike a review, a sample run has the network: the clone, the dataset fetch
and whatever a generator downloads all work. Reviews, which run with no
network, are a separate design.

## Parameters

| Name | Meaning | Default |
|---|---|---|
| `REPOSITORY` | The target app's GitHub repository URL | required |
| `TAG` | Its release tag; `app.yml` is read at that tag | required |
| `PORTS` | Comma-separated input ports to build; empty builds every port the review block covers | all |
| `MAX_DATASET_BYTES` | The most a dataset fetch may download | 2 GiB |
| `GENERATE_TIMEOUT` | Seconds a generator command may run | 900 |
| `BUNDLE_URL` | A presigned URL of a fixture bundle the release pipeline built for this app and tag; set, it is unpacked instead of cloning and generating (`MAX_DATASET_BYTES` caps the download) | none |

No inputs. One output, `fixtures`, which is the whole output directory.

## In a sample run

A sample workflow has three nodes: this app (parameters `REPOSITORY`, `TAG`),
the target app depending on it with no port links and the review block's
`parameters` as its parameter values, and a download target. The target app
then reads its usual `INPUT_DIR`.

The provenance crate sits in a dot-folder so that an app which globs the root
of its input directory does not pick it up.

### From a bundle

The App Store builds these fixtures once per release (app-deploy-service,
"Fixture bundles") and keeps the output directory as a `.tar.gz`. A sample
run on a node that may not have internet, or that should not fetch again,
passes that bundle's presigned URL as `BUNDLE_URL`: the app unpacks it into
the output, prints one `FIXTURES_BUNDLE {...}` line, and exits 0. A bundle
that records a failed build (`.pennsieve-fixtures/error.json`) is reported
exactly as that failure would be here: the same `FIXTURES_ERROR` line and
exit status. The bundle must be a plain tar.gz of regular files;
anything else (links, paths outside the output) is skipped.

## Developing

```
make test        # unit tests: no network, no Docker
make run         # build the fixtures of the app in dev.env, in Docker, into data/output/
```

The end-to-end test uses a local git repository as the target, so it runs
offline. Dataset hosts are exercised against recorded responses.

## When it cannot build

Every failure is reported three ways so both a person and a service can act
on it: a log line, one `FIXTURES_ERROR {...}` line in the log, and
`.pennsieve-fixtures/error.json` in the output. The JSON has a stable `code`,
the `message`, the `port` when one is at fault, the `target` (repository, tag,
commit when cloned), and, when the app's own declaration is the problem, a
`hint` for its author and a `docs` link.

| Exit | Meaning | Codes |
|---|---|---|
| `3` | The app declares no usable test data; its author must change `app.yml`. A sample run should show this as "no test data declared", not as a failed run. | `no_review_block`, `no_source_for_port`, `unknown_port`, `invalid_review_block`, `files_not_found` |
| `1` | The declared test data could not be built here. | `clone_failed`, `generator_failed`, `generator_image_unavailable`, `dataset_unavailable`, `dataset_too_large`, `empty_port`, `bundle_unavailable`, `bundle_too_large`, `bundle_invalid`, `internal` |
| `2` | Missing or invalid parameters (`OUTPUT_DIR`, `REPOSITORY`, `TAG`). | |

An app without a `review` block is the common case for releases made before
the block existed: the fix is a new release that carries one.

## License

Apache-2.0. Built by Pennsieve (University of Pennsylvania).
